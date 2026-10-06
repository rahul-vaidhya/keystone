"""Retrieval ablation on BEIR SciFact.

Run from `backend/`:
    .venv/Scripts/python.exe -m eval.run_ablation --k 10
    .venv/Scripts/python.exe -m eval.run_ablation --methods dense bm25 --limit-queries 50
    .venv/Scripts/python.exe -m eval.run_ablation --with-reranker   # needs a reachable TEI

Writes `eval/results/scifact_ablation.md`, `scifact_per_query.json` and
`sparse_vs_dense.md`. Dense vectors are cached under `eval/data/` so the embedding cost
is paid once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import numpy as np

from eval import metrics
from eval.scifact import DATA_DIR, SciFact, load

RESULTS_DIR = Path(__file__).resolve().parent / "results"
POOL = 100  # depth retrieved per method (R@100 and the RRF input lists)
RRF_K = 60
LOCAL_DENSE_MODEL = "BAAI/bge-small-en-v1.5"
ALL_METHODS = [
    "tfidf",
    "bm25",
    "bm25_zones",
    "bm25_champions",
    "dense",
    "hybrid_rrf",
    "hybrid_rrf_rerank",
]
SPARSE_METHODS = {
    "tfidf": {"scheme": "tfidf", "zone_weights": None, "use_champions": False},
    "bm25": {"scheme": "bm25", "zone_weights": None, "use_champions": False},
    "bm25_zones": {
        "scheme": "bm25",
        "zone_weights": {"title": 2.0, "body": 1.0},
        "use_champions": False,
    },
    "bm25_champions": {"scheme": "bm25", "zone_weights": None, "use_champions": True},
}

Run = dict[str, list[str]]  # query_id -> ranked doc ids


# --------------------------------------------------------------------------- sparse


class SparseBackend:
    def __init__(self, data: SciFact) -> None:
        from app.services.retrieval.sparse import InvertedIndex, SparseDoc, search

        self._search = search
        docs = [
            SparseDoc(doc_id=d.doc_id, zones={"title": d.title, "body": d.text})
            for d in data.corpus
        ]
        t0 = time.perf_counter()
        self.index = InvertedIndex.build(docs, champion_r=50)
        self.build_seconds = time.perf_counter() - t0

    def stats(self) -> dict[str, object]:
        idx = self.index
        terms = idx.terms()
        doc_lens = [len(idx.doc_postings(t)) for t in terms]
        zone_lens = {
            z: round(statistics.mean(len(idx.postings(t, z)) for t in terms), 2) for z in idx.zones
        }
        return {
            "n_docs": idx.n_docs,
            "vocabulary_size": idx.vocabulary_size(),
            "avg_doc_postings_length": round(statistics.mean(doc_lens), 2) if doc_lens else 0,
            "total_doc_postings": sum(doc_lens),
            "avg_zone_postings_length": zone_lens,
            "champion_r": idx.champion_r,
            "index_build_s": round(self.build_seconds, 2),
        }

    def run(self, queries: dict[str, str], **kwargs) -> tuple[Run, list[float]]:
        run: Run = {}
        lat: list[float] = []
        for qid, text in queries.items():
            t0 = time.perf_counter()
            hits = self._search(self.index, text, k=POOL, **kwargs)
            lat.append((time.perf_counter() - t0) * 1000)
            run[qid] = [h.doc_id for h in hits]
        return run, lat


# --------------------------------------------------------------------------- dense


def _doc_text(title: str, text: str) -> str:
    return f"{title}\n\n{text}".strip()[:24000]  # well under the 8k-token model limit


async def _embed_all(embedder, texts, batch=96, concurrency=4, is_query=False):
    sem = asyncio.Semaphore(concurrency)
    out: list[list[float] | None] = [None] * len(texts)

    async def one(start: int) -> None:
        chunk = texts[start : start + batch]
        async with sem:
            for attempt in range(5):
                try:
                    vecs = await (
                        embedder.embed(chunk, is_query=True) if is_query else embedder.embed(chunk)
                    )
                    break
                except Exception:  # noqa: BLE001 — retry transient vendor errors
                    if attempt == 4:
                        raise
                    await asyncio.sleep(2**attempt)
        for i, v in enumerate(vecs):
            out[start + i] = v

    await asyncio.gather(*(one(s) for s in range(0, len(texts), batch)))
    return out


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


def _cache_paths(name: str, model: str) -> tuple[Path, Path]:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    return DATA_DIR / f"{name}_{safe}.npy", DATA_DIR / f"{name}_{safe}_ids.json"


def _has_cache(model: str) -> bool:
    return all(p.exists() for p in _cache_paths("dense_cache", model))


def _cached_embeddings(
    name: str,
    ids: list[str],
    texts: list[str],
    embedder,
    is_query: bool = False,
    batch: int = 96,
    concurrency: int = 4,
) -> np.ndarray:
    vec_path, id_path = _cache_paths(name, embedder.model)
    if vec_path.exists() and id_path.exists():
        cached_ids = json.loads(id_path.read_text(encoding="utf-8"))
        if cached_ids == ids:
            return np.load(vec_path)
    t0 = time.perf_counter()
    vecs = asyncio.run(_embed_all(embedder, texts, batch, concurrency, is_query))
    mat = _normalize(np.asarray(vecs, dtype=np.float32))
    np.save(vec_path, mat)
    id_path.write_text(json.dumps(ids), encoding="utf-8")
    chars = sum(len(t) for t in texts)
    print(
        f"[dense] embedded {len(texts)} {name} texts ({chars:,} chars, ~{chars // 4:,} tokens) "
        f"in {time.perf_counter() - t0:.1f}s with {embedder.model}"
    )
    return mat


class LocalEmbedder:
    """Offline fallback dense encoder (fastembed / ONNX, no API key). Same `model` +
    async `embed` shape as the app's `Embedder` seam; `is_query` applies the model's
    query instruction (BGE models expect one)."""

    def __init__(self, model: str = LOCAL_DENSE_MODEL) -> None:
        from fastembed import TextEmbedding

        self._model = model
        self._enc = TextEmbedding(model_name=model)

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        fn = self._enc.query_embed if is_query else self._enc.embed
        return [v.tolist() for v in fn(texts)]


def _seam_embedder():
    from app.config.settings import settings
    from app.services.seams.factory import get_embedder

    if settings.EMBEDDER_MODE != "real":
        settings.EMBEDDER_MODE = "real"  # in-process only; .env is never touched
    return get_embedder()


def _seam_usable(embedder) -> bool:
    try:
        asyncio.run(embedder.embed(["probe"]))
        return True
    except Exception as exc:  # noqa: BLE001 — auth/config failure -> caller falls back
        print(f"[dense] embedder seam unusable ({type(exc).__name__}: {str(exc)[:120]})")
        return False


class DenseUnavailable(RuntimeError):
    pass


class DenseBackend:
    def __init__(self, data: SciFact, queries: dict[str, str], backend: str = "auto") -> None:
        self.embedder = None
        if backend in ("auto", "seam"):
            seam = _seam_embedder()
            if _has_cache(seam.model) or _seam_usable(seam):
                self.embedder = seam
            else:
                raise DenseUnavailable(
                    "embedder seam failed (check OPENAI_API_KEY/OPENAI_BASE_URL in backend/.env); "
                    "rerun with a valid key, or pass --dense-backend local for an offline "
                    f"fastembed {LOCAL_DENSE_MODEL} run (slow on CPU)"
                )
        if self.embedder is None:
            print(f"[dense] using local fastembed model {LOCAL_DENSE_MODEL}")
            self.embedder = LocalEmbedder()
        local = isinstance(self.embedder, LocalEmbedder)
        self.doc_ids = [d.doc_id for d in data.corpus]
        self.docs = _cached_embeddings(
            "dense_cache",
            self.doc_ids,
            [_doc_text(d.title, d.text) for d in data.corpus],
            self.embedder,
            batch=256 if local else 96,
            concurrency=1 if local else 4,
        )
        all_qids = list(data.queries)
        qmat = _cached_embeddings(
            "dense_queries",
            all_qids,
            [data.queries[q] for q in all_qids],
            self.embedder,
            is_query=local,
            batch=256 if local else 96,
            concurrency=1 if local else 4,
        )
        self.qvec = {q: qmat[i] for i, q in enumerate(all_qids)}
        self.queries = queries

    def run(self, queries: dict[str, str]) -> tuple[Run, list[float]]:
        run: Run = {}
        lat: list[float] = []
        for qid in queries:
            t0 = time.perf_counter()
            scores = self.docs @ self.qvec[qid]
            top = np.argpartition(-scores, POOL)[:POOL]
            top = top[np.argsort(-scores[top])]
            lat.append((time.perf_counter() - t0) * 1000)
            run[qid] = [self.doc_ids[i] for i in top]
        return run, lat


# --------------------------------------------------------------------------- fusion / rerank


def rrf(lists: list[list[str]], k: int = RRF_K, depth: int = POOL) -> list[str]:
    scores: Counter[str] = Counter()
    for ranked in lists:
        for rank, doc_id in enumerate(ranked[:depth], start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return [d for d, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))][:depth]


def _reranker_reachable(url: str | None) -> bool:
    if not url:
        return False
    import httpx

    try:
        return httpx.get(f"{url}/health", timeout=5.0).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def rerank_run(base: Run, queries: dict[str, str], doc_text: dict[str, str], url: str, depth=50):
    import httpx

    run: Run = {}
    lat: list[float] = []
    with httpx.Client(timeout=300.0) as client:
        for qid, ranked in base.items():
            cands = ranked[:depth]
            t0 = time.perf_counter()
            resp = client.post(
                f"{url}/rerank",
                json={
                    "query": queries[qid],
                    "texts": [doc_text[d] for d in cands],
                    "raw_scores": False,
                },
            )
            resp.raise_for_status()
            order = sorted(resp.json(), key=lambda r: -r["score"])
            lat.append((time.perf_counter() - t0) * 1000)
            reranked = [cands[r["index"]] for r in order]
            run[qid] = reranked + [d for d in ranked if d not in set(reranked)]
    return run, lat


# --------------------------------------------------------------------------- reporting

COLS = ["P@1", "P@5", "P@10", "R@10", "R@100", "MRR@10", "nDCG@10"]


def _table(summary: dict[str, dict]) -> str:
    head = "| method | " + " | ".join(COLS) + " | mean query ms |"
    sep = "|" + "---|" * (len(COLS) + 2)
    rows = [head, sep]
    for method, s in summary.items():
        vals = " | ".join(f"{s['metrics'][c]:.4f}" for c in COLS)
        rows.append(f"| {method} | {vals} | {s['latency_ms']:.2f} |")
    return "\n".join(rows)


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def sparse_vs_dense(per_query, queries, data: SciFact, a="bm25", b="dense", margin=0.1) -> str:
    df: Counter[str] = Counter()
    for d in data.corpus:
        df.update(set(_tokens(f"{d.title} {d.text}")))
    n = len(data.corpus)

    def idf(t: str) -> float:
        return math.log(n / (1 + df.get(t, 0)))

    high_idf = math.log(n / 101)  # term in <= ~100 docs (<2% of the corpus)
    buckets: dict[str, list[str]] = {f"{a}_wins": [], f"{b}_wins": [], "ties": []}
    for qid in queries:
        sa = per_query[a][qid]["nDCG@10"]
        sb = per_query[b][qid]["nDCG@10"]
        key = f"{a}_wins" if sa - sb > margin else f"{b}_wins" if sb - sa > margin else "ties"
        buckets[key].append(qid)

    def profile(qids: list[str]) -> dict[str, float]:
        if not qids:
            return {}
        lens, hi, dig, oov, mean_idf = [], [], [], [], []
        for q in qids:
            toks = _tokens(queries[q])
            lens.append(len(toks))
            hi.append(sum(idf(t) >= high_idf for t in toks) / max(len(toks), 1))
            dig.append(float(any(re.search(r"\d", t) for t in toks)))
            oov.append(sum(df.get(t, 0) == 0 for t in toks) / max(len(toks), 1))
            mean_idf.append(statistics.mean(idf(t) for t in toks) if toks else 0.0)
        return {
            "n_queries": len(qids),
            "avg_query_len_tokens": statistics.mean(lens),
            "frac_high_idf_terms": statistics.mean(hi),
            "frac_queries_with_digits": statistics.mean(dig),
            "frac_terms_unseen_in_corpus": statistics.mean(oov),
            "mean_term_idf": statistics.mean(mean_idf),
        }

    lines = [
        f"# Sparse ({a}) vs dense ({b}) on SciFact test",
        "",
        f"Per-query nDCG@10 compared; a 'win' needs a margin > {margin}.",
        "",
        "| bucket | queries |",
        "|---|---|",
    ]
    lines += [f"| {k} | {len(v)} |" for k, v in buckets.items()]
    lines += ["", "## Query characterization per bucket", ""]
    feats = [
        "n_queries",
        "avg_query_len_tokens",
        "frac_high_idf_terms",
        "frac_queries_with_digits",
        "frac_terms_unseen_in_corpus",
        "mean_term_idf",
    ]
    lines.append("| bucket | " + " | ".join(feats) + " |")
    lines.append("|" + "---|" * (len(feats) + 1))
    for k, v in buckets.items():
        p = profile(v)
        lines.append(f"| {k} | " + " | ".join(f"{p.get(f, 0):.3f}" for f in feats) + " |")
    lines += [
        "",
        f"High-idf term = appears in at most ~100 of {n} docs (idf >= {high_idf:.2f}).",
        "",
    ]
    for key in (f"{a}_wins", f"{b}_wins"):
        lines += [f"## Examples: {key}", ""]
        ex = sorted(
            buckets[key],
            key=lambda q: -abs(per_query[a][q]["nDCG@10"] - per_query[b][q]["nDCG@10"]),
        )[:5]
        for q in ex:
            lines.append(
                f"- `{q}` ({a} {per_query[a][q]['nDCG@10']:.2f} vs {b} "
                f"{per_query[b][q]['nDCG@10']:.2f}): {queries[q]}"
            )
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--k", type=int, default=10, help="cutoff for the headline P/R columns")
    ap.add_argument("--methods", nargs="+", default=None, choices=ALL_METHODS)
    ap.add_argument("--limit-queries", type=int, default=None)
    ap.add_argument("--with-reranker", action="store_true")
    ap.add_argument(
        "--dense-backend",
        choices=["auto", "seam", "local"],
        default="auto",
        help="auto/seam: app Embedder seam (EMBEDDER_MODE=real; dense methods skip if it fails); "
        "local: offline fastembed encoder",
    )
    args = ap.parse_args(argv)

    data = load()
    queries = dict(sorted(data.queries.items(), key=lambda kv: int(kv[0])))
    if args.limit_queries:
        queries = dict(list(queries.items())[: args.limit_queries])
    methods = args.methods or [m for m in ALL_METHODS if m != "hybrid_rrf_rerank"]
    if args.with_reranker and "hybrid_rrf_rerank" not in methods:
        methods.append("hybrid_rrf_rerank")
    print(f"SciFact: {len(data.corpus)} docs, {len(queries)} test queries, methods={methods}")

    runs: dict[str, Run] = {}
    lats: dict[str, list[float]] = {}
    sparse: SparseBackend | None = None
    dense: DenseBackend | None = None
    index_stats: dict[str, object] = {}

    def need_sparse() -> SparseBackend | None:
        nonlocal sparse, index_stats
        if sparse is None:
            try:
                sparse = SparseBackend(data)
                index_stats = sparse.stats()
            except ImportError as exc:
                print(f"[skip] sparse package unavailable ({exc}); sparse methods skipped")
                return None
        return sparse

    dense_failed = False

    def need_dense() -> DenseBackend | None:
        nonlocal dense, dense_failed
        if dense is None and not dense_failed:
            try:
                dense = DenseBackend(data, queries, args.dense_backend)
            except DenseUnavailable as exc:
                dense_failed = True
                print(f"[skip] dense/hybrid methods: {exc}")
        return dense

    plan: list[tuple[str, Callable[[], tuple[Run, list[float]] | None]]] = []
    for m in methods:
        if m in SPARSE_METHODS:
            plan.append(
                (
                    m,
                    lambda m=m: (
                        s.run(queries, **SPARSE_METHODS[m]) if (s := need_sparse()) else None
                    ),
                )
            )
        elif m == "dense":
            plan.append((m, lambda: d.run(queries) if (d := need_dense()) else None))

    for name, fn in plan:
        res = fn()
        if res is not None:
            runs[name], lats[name] = res

    if "hybrid_rrf" in methods or "hybrid_rrf_rerank" in methods:
        if "bm25" not in runs and need_sparse():
            runs_bm25, _ = sparse.run(queries, **SPARSE_METHODS["bm25"])  # type: ignore[union-attr]
        else:
            runs_bm25 = runs.get("bm25")
        if "dense" not in runs and (d := need_dense()):
            runs["dense"], lats["dense"] = d.run(queries)
        if runs_bm25 is None or "dense" not in runs:
            print("[skip] hybrid_rrf needs both bm25 and dense results")
        else:
            hyb: Run = {}
            hl: list[float] = []
            for qid in queries:
                t0 = time.perf_counter()
                hyb[qid] = rrf([runs_bm25[qid], runs["dense"][qid]])
                hl.append((time.perf_counter() - t0) * 1000)
            bm_lat = lats.get("bm25") or [0.0]
            de_lat = lats.get("dense") or [0.0]
            if "hybrid_rrf" in methods:
                runs["hybrid_rrf"] = hyb
                lats["hybrid_rrf"] = [
                    statistics.mean(bm_lat) + statistics.mean(de_lat) + h for h in hl
                ]
            if "hybrid_rrf_rerank" in methods:
                from app.config.settings import settings

                if _reranker_reachable(settings.RERANKER_URL):
                    texts = {d.doc_id: _doc_text(d.title, d.text) for d in data.corpus}
                    runs["hybrid_rrf_rerank"], lats["hybrid_rrf_rerank"] = rerank_run(
                        hyb, queries, texts, settings.RERANKER_URL
                    )
                else:
                    print(
                        f"[skip] hybrid_rrf_rerank: no TEI reranker reachable at "
                        f"RERANKER_URL={settings.RERANKER_URL!r} (start it with "
                        "`docker compose up -d reranker` and retry with --with-reranker)"
                    )

    per_query = {
        m: {q: metrics.evaluate_query(run[q], data.qrels[q]) for q in queries}
        for m, run in runs.items()
    }
    summary = {
        m: {"metrics": metrics.average(per_query[m]), "latency_ms": statistics.mean(lats[m])}
        for m in [x for x in ALL_METHODS if x in runs]
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    table = _table(summary)
    md = [
        "# SciFact retrieval ablation (BEIR test split)",
        "",
        f"{len(data.corpus)} docs, {len(queries)} queries, retrieval depth {POOL}. "
        "Latency = mean per-query search time in this process (dense excludes the query "
        "embedding API call, which is cached; hybrid = bm25 + dense + fusion; rerank = TEI "
        "round-trip over the top 50).",
        "",
        table,
        "",
    ]
    if index_stats:
        md += ["## Sparse index stats", ""] + [f"- {k}: {v}" for k, v in index_stats.items()] + [""]
    if dense is not None:
        md += [f"Dense model: `{dense.embedder.model}` (cosine, exact brute-force kNN).", ""]
    (RESULTS_DIR / "scifact_ablation.md").write_text("\n".join(md), encoding="utf-8")
    (RESULTS_DIR / "scifact_per_query.json").write_text(
        json.dumps(
            {
                m: {q: {"metrics": per_query[m][q], "top10": runs[m][q][:10]} for q in queries}
                for m in per_query
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(table)
    if index_stats:
        print("index stats:", index_stats)
    if "bm25" in per_query and "dense" in per_query:
        report = sparse_vs_dense(per_query, queries, data)
        (RESULTS_DIR / "sparse_vs_dense.md").write_text(report, encoding="utf-8")
        print("wrote sparse_vs_dense.md")
    print(f"results in {RESULTS_DIR}")


if __name__ == "__main__":
    main()
