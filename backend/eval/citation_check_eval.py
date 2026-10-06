"""Measured accuracy of the per-sentence citation checker on SciFact claims.

Run from `backend/`:
    .venv/Scripts/python.exe -m eval.citation_check_eval

Each (claim, abstract) pair is framed exactly as the app frames "an answer sentence +
the chunk it cites": the claim is one sentence citing ``[1]``, the abstract (title +
abstract text, the same string the dense cache embedded) is ``ContextBlock`` 1, and the
app's own ``score_claims`` computes lexical / semantic / combined scores and the
``supported`` / ``weak`` status. Lexical idf comes from the from-scratch sparse
``InvertedIndex`` built over the whole SciFact corpus.

Pairs (per split):
- support    — claim + an evidence abstract labelled SUPPORT          (positive)
- contradict — claim + an evidence abstract labelled CONTRADICT        (reported apart)
- hard_neg   — claim + top-BM25 abstract not in its evidence/cited set (negative)
- easy_neg   — claim + a random abstract not in its evidence/cited set (negative)

Data: the original AllenAI SciFact release (claims with sentence-level rationales),
cached under eval/data/ and read straight out of the tarball (never extracted). The dev
split is the reported split (test labels are hidden); train is used to tune thresholds.

Writes eval/results/citation_check_eval.md and citation_check_pairs.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tarfile
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.config.settings import settings
from app.models.retrieval import ContextBlock
from app.services.chat.citation_check import chunk_windows, score_claims, strip_markers
from app.services.retrieval.sparse import InvertedIndex, SparseDoc, search, tfidf_cosine
from eval.classification import best_f1_threshold, confusion_at, roc_auc
from eval.run_ablation import _cache_paths, _doc_text, _embed_all, _normalize, _seam_embedder
from eval.scifact import DATA_DIR
from eval.scifact import load as load_beir

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RELEASE_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
RELEASE_PATH = DATA_DIR / "scifact_release.tar.gz"
APP_THRESHOLD = settings.CITATION_SUPPORT_THRESHOLD
SIGNALS = ("lexical", "semantic", "combined")
SEED = 13


# --------------------------------------------------------------------------- data


def ensure_release() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not RELEASE_PATH.exists():
        tmp = RELEASE_PATH.with_suffix(".part")
        with urllib.request.urlopen(RELEASE_URL, timeout=120) as resp, open(tmp, "wb") as fh:  # noqa: S310
            fh.write(resp.read())
        tmp.replace(RELEASE_PATH)
    return RELEASE_PATH


def _read_jsonl(tf: tarfile.TarFile, name: str) -> list[dict]:
    member = tf.extractfile(name)
    if member is None:
        raise FileNotFoundError(name)
    return [json.loads(line) for line in member.read().decode("utf-8").splitlines() if line.strip()]


def load_release() -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """``(abstract sentences by doc_id, {"train": claims, "dev": claims})`` — parsed only."""
    with tarfile.open(ensure_release(), "r:gz") as tf:
        corpus = {
            str(r["doc_id"]): [str(s) for s in r["abstract"]]
            for r in _read_jsonl(tf, "data/corpus.jsonl")
        }
        claims = {
            "train": _read_jsonl(tf, "data/claims_train.jsonl"),
            "dev": _read_jsonl(tf, "data/claims_dev.jsonl"),
        }
    return corpus, claims


@dataclass
class Pair:
    split: str
    kind: str  # support | contradict | hard_neg | easy_neg
    claim_id: str
    claim: str
    doc_id: str
    gold_sentences: list[int] = field(default_factory=list)
    lexical: float | None = None
    semantic: float | None = None
    combined: float | None = None
    status: str = ""
    best_window: str = ""
    loc: dict[str, object] = field(default_factory=dict)


def build_pairs(
    claims: dict[str, list[dict]], index: InvertedIndex, doc_ids: list[str]
) -> list[Pair]:
    pairs: list[Pair] = []
    for split, rows in claims.items():
        for row in rows:
            cid, claim = str(row["id"]), str(row["claim"])
            evidence = {str(d): sets for d, sets in (row.get("evidence") or {}).items()}
            excluded = set(evidence) | {str(d) for d in row.get("cited_doc_ids") or []}
            for doc_id, sets in evidence.items():
                labels = {s["label"] for s in sets}
                if len(labels) != 1:
                    continue  # mixed labels for one doc: ambiguous, skip
                gold = sorted({i for s in sets for i in s["sentences"]})
                kind = "support" if labels == {"SUPPORT"} else "contradict"
                pairs.append(Pair(split, kind, cid, claim, doc_id, gold))
            hard = next(
                (h.doc_id for h in search(index, claim, k=50) if h.doc_id not in excluded), None
            )
            if hard is not None:
                pairs.append(Pair(split, "hard_neg", cid, claim, hard))
            rng = random.Random(f"{SEED}:{split}:{cid}")
            while True:
                easy = rng.choice(doc_ids)
                if easy not in excluded:
                    break
            pairs.append(Pair(split, "easy_neg", cid, claim, easy))
    return pairs


# --------------------------------------------------------------------------- vectors


def _load_cache(name: str, model: str) -> dict[str, np.ndarray]:
    vec_path, id_path = _cache_paths(name, model)
    if not (vec_path.exists() and id_path.exists()):
        return {}
    ids = json.loads(id_path.read_text(encoding="utf-8"))
    mat = np.load(vec_path)
    return {i: mat[k] for k, i in enumerate(ids)}


def claim_vectors(
    texts: list[str], model: str, beir_queries: dict[str, str]
) -> tuple[dict[str, np.ndarray], int]:
    """Embedding per claim text. Reuses the ablation's query cache (dev claims ARE the
    BEIR SciFact test queries) and a claim cache of our own; embeds the rest through
    the real ``Embedder`` seam. Returns ``(vectors, chars_embedded_now)``."""
    out: dict[str, np.ndarray] = {}
    qcache = _load_cache("dense_queries", model)
    for qid, text in beir_queries.items():
        if qid in qcache:
            out[text] = qcache[qid]
    own_path, own_ids = _cache_paths("citation_claims", model)
    if own_path.exists() and own_ids.exists():
        ids = json.loads(own_ids.read_text(encoding="utf-8"))
        mat = np.load(own_path)
        out.update({t: mat[k] for k, t in enumerate(ids)})
    missing = [t for t in dict.fromkeys(texts) if t not in out]
    chars = 0
    if missing:
        embedder = _seam_embedder()
        if embedder.model != model:
            raise RuntimeError(f"embedder model {embedder.model!r} != cached doc model {model!r}")
        vecs = _normalize(np.asarray(asyncio.run(_embed_all(embedder, missing)), dtype=np.float32))
        chars = sum(len(t) for t in missing)
        prev_ids = json.loads(own_ids.read_text(encoding="utf-8")) if own_ids.exists() else []
        prev = np.load(own_path) if own_path.exists() else np.zeros((0, vecs.shape[1]), np.float32)
        np.save(own_path, np.vstack([prev, vecs]))
        own_ids.write_text(json.dumps(prev_ids + missing), encoding="utf-8")
        out.update(dict(zip(missing, vecs, strict=True)))
        print(
            f"[embed] {len(missing)} claims, {chars:,} chars (~{chars // 4:,} tokens) via {model}"
        )
    return out, chars


# --------------------------------------------------------------------------- localization


def _norm(s: str) -> str:
    return " ".join(s.split())


def _spans(chunk: str, pieces: list[str]) -> list[tuple[int, int] | None]:
    """Character span of each piece inside the whitespace-normalized chunk, searched in
    order (each piece after the previous one's start)."""
    text, cursor, out = _norm(chunk), 0, []
    for p in pieces:
        pos = text.find(_norm(p), cursor)
        if pos < 0:
            pos = text.find(_norm(p))
        if pos < 0:
            out.append(None)
            continue
        out.append((pos, pos + len(_norm(p))))
        cursor = pos
    return out


def _overlaps(a: tuple[int, int] | None, golds: list[tuple[int, int]]) -> bool:
    return a is not None and any(a[0] < g[1] and g[0] < a[1] for g in golds)


def localize(pair: Pair, chunk: str, sentences: list[str], lexical_fn) -> dict[str, object] | None:
    sent_spans = _spans(chunk, sentences)
    golds = [sent_spans[i] for i in pair.gold_sentences if i < len(sent_spans) and sent_spans[i]]
    if not golds:
        return None
    windows = chunk_windows(chunk)
    scores = [lexical_fn(strip_markers(pair.claim), w) for w in windows]
    text = _norm(chunk)
    starts = [text.find(_norm(w)) for w in windows]
    win_spans = [
        (s, s + len(_norm(w))) if s >= 0 else None for s, w in zip(starts, windows, strict=True)
    ]
    best = max(range(len(windows)), key=lambda i: scores[i])  # first max, like the app's max()
    # chunk_windows = n single-sentence parts followed by n-1 adjacent pairs
    n_parts = (len(windows) + 1) // 2
    best_single = max(range(n_parts), key=lambda i: scores[i])
    hits = [_overlaps(s, golds) for s in win_spans]
    first_sentence = sent_spans[0]
    first_pair_span = win_spans[n_parts] if len(windows) > n_parts else win_spans[0]
    return {
        "best_window_hit": hits[best],
        "best_single_hit": hits[best_single],
        "first_sentence_hit": 0 in pair.gold_sentences or _overlaps(first_sentence, golds),
        "first_pair_hit": _overlaps(first_pair_span, golds),
        "random_window_hit": sum(hits) / len(hits),
        "random_single_hit": sum(hits[:n_parts]) / n_parts,
        "n_windows": len(windows),
        "best_window_index": best,
        "best_span": win_spans[best],
    }


# --------------------------------------------------------------------------- report


def _scores(pairs: list[Pair], signal: str) -> list[float]:
    return [(getattr(p, signal) or 0.0) for p in pairs]


def _binary(pairs: list[Pair], negatives: set[str]) -> list[Pair]:
    return [p for p in pairs if p.kind == "support" or p.kind in negatives]


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def evaluate(pairs: list[Pair]) -> tuple[str, dict]:
    train = [p for p in pairs if p.split == "train"]
    dev = [p for p in pairs if p.split == "dev"]
    settings_ = {"hard": {"hard_neg"}, "easy": {"easy_neg"}, "all": {"hard_neg", "easy_neg"}}
    summary: dict = {"tuned": {}, "dev": {}}
    lines: list[str] = []

    def counts(ps: list[Pair]) -> str:
        from collections import Counter

        c = Counter(p.kind for p in ps)
        return ", ".join(
            f"{k}={c.get(k, 0)}" for k in ("support", "contradict", "hard_neg", "easy_neg")
        )

    lines += [
        "# Citation checker accuracy on SciFact claims",
        "",
        "Each pair = one claim sentence citing one abstract `[1]`, scored by the app's own "
        "`score_claims` (lexical = best tf-idf ltc cosine over the abstract's sentence windows, "
        "idf from the from-scratch sparse index over all 5,183 SciFact abstracts; semantic = "
        "`text-embedding-3-small` cosine vs the whole abstract; combined = mean). "
        f"App threshold: `CITATION_SUPPORT_THRESHOLD = {APP_THRESHOLD}` "
        "(supported iff combined >= it).",
        "",
        f"- **dev** (reported): {counts(dev)}",
        f"- **train** (threshold tuning only): {counts(train)}",
        "- positive = SUPPORT evidence abstract; hard_neg = top-BM25 abstract outside the claim's "
        'evidence/cited set ("cited the wrong but on-topic chunk"); easy_neg = random abstract.',
        "",
        "## Mean scores by pair kind (dev)",
        "",
        "| kind | n | lexical | semantic | combined | % called supported @ app threshold |",
        "|---|---|---|---|---|---|",
    ]
    for kind in ("support", "contradict", "hard_neg", "easy_neg"):
        ps = [p for p in dev if p.kind == kind]
        if not ps:
            continue
        sup = sum((p.combined or 0) >= APP_THRESHOLD for p in ps) / len(ps)
        lines.append(
            f"| {kind} | {len(ps)} | "
            + " | ".join(_fmt(float(np.mean(_scores(ps, s)))) for s in SIGNALS)
            + f" | {sup:.1%} |"
        )

    for name, negs in settings_.items():
        tr, dv = _binary(train, negs), _binary(dev, negs)
        ytr = [p.kind == "support" for p in tr]
        ydv = [p.kind == "support" for p in dv]
        lines += [
            "",
            f"## SUPPORT vs {name} negatives (dev: {sum(ydv)} pos / {len(ydv) - sum(ydv)} neg)",
            "",
            "| signal | ROC-AUC | P @0.33 | R @0.33 | F1 @0.33 | Acc @0.33 | train best-F1 thr | "
            "dev P @thr | dev R @thr | dev F1 @thr | dev Acc @thr | dev oracle F1 (thr) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for sig in SIGNALS:
            sdv, strn = _scores(dv, sig), _scores(tr, sig)
            auc = roc_auc(sdv, ydv)
            c = confusion_at(sdv, ydv, APP_THRESHOLD)
            thr, _ = best_f1_threshold(strn, ytr)
            ct = confusion_at(sdv, ydv, thr)
            othr, of1 = best_f1_threshold(sdv, ydv)
            summary["dev"].setdefault(name, {})[sig] = {
                "auc": auc,
                "at_app": vars(c)
                | {"p": c.precision, "r": c.recall, "f1": c.f1, "acc": c.accuracy},
                "train_thr": thr,
                "at_train_thr": {
                    "p": ct.precision,
                    "r": ct.recall,
                    "f1": ct.f1,
                    "acc": ct.accuracy,
                },
                "oracle": {"thr": othr, "f1": of1},
            }
            lines.append(
                f"| {sig} | {_fmt(auc)} | {_fmt(c.precision)} | {_fmt(c.recall)} | {_fmt(c.f1)} | "
                f"{_fmt(c.accuracy)} | {thr:.3f} | {_fmt(ct.precision)} | {_fmt(ct.recall)} | "
                f"{_fmt(ct.f1)} | {_fmt(ct.accuracy)} | {_fmt(of1)} ({othr:.3f}) |"
            )

    # SUPPORT vs CONTRADICT: the checker is not an entailment model
    sc = [p for p in dev if p.kind in ("support", "contradict")]
    ysc = [p.kind == "support" for p in sc]
    contra = [p for p in dev if p.kind == "contradict"]
    lines += [
        "",
        "## Contradicted claims (dev) — a measured limitation",
        "",
        "| signal | ROC-AUC SUPPORT vs CONTRADICT | % CONTRADICT pairs called supported @0.33 |",
        "|---|---|---|",
    ]
    summary["contradict"] = {}
    for sig in SIGNALS:
        auc = roc_auc(_scores(sc, sig), ysc)
        rate = sum(v >= APP_THRESHOLD for v in _scores(contra, sig)) / len(contra)
        summary["contradict"][sig] = {
            "auc_support_vs_contradict": auc,
            "false_supported_rate": rate,
        }
        lines.append(f"| {sig} | {_fmt(auc)} | {rate:.1%} |")

    # localization
    loc = [p.loc for p in dev if p.kind == "support" and p.loc]
    loc_tr = [p.loc for p in train if p.kind == "support" and p.loc]
    n_sup = sum(p.kind == "support" for p in dev)

    def rate(rows: list[dict], key: str) -> float:
        return sum(float(r[key]) for r in rows) / len(rows) if rows else 0.0

    summary["localization"] = {
        k: rate(loc, k)
        for k in (
            "best_window_hit",
            "best_single_hit",
            "first_sentence_hit",
            "first_pair_hit",
            "random_window_hit",
            "random_single_hit",
            "app_view_best_hit",
            "app_view_title_best",
        )
    }
    summary["localization"]["n"] = len(loc)
    lines += [
        "",
        f"## Evidence localization (SUPPORT pairs, dev n={len(loc)} of {n_sup}; "
        f"train n={len(loc_tr)})",
        "",
        "Does the checker's best-matching passage (argmax lexical window — the passage behind "
        "the lexical score) overlap a gold rationale sentence? Primary rows use windows over "
        "the abstract text only (the gold indices refer to it); the app-view rows use the full "
        "chunk the checker actually sees (title + abstract), where a best-matching title counts "
        "as a miss.",
        "",
        "| method | dev hit@1 | train hit@1 |",
        "|---|---|---|",
    ]
    loc_rows = [
        ("checker best window (single sentence or adjacent pair)", "best_window_hit"),
        ("checker best single-sentence window", "best_single_hit"),
        ("baseline: first abstract sentence", "first_sentence_hit"),
        ("baseline: first adjacent-pair window", "first_pair_hit"),
        ("baseline: random window (expected)", "random_window_hit"),
        ("baseline: random single sentence (expected)", "random_single_hit"),
        ("app view (title + abstract windows): checker best window", "app_view_best_hit"),
        ("app view: share of pairs where the TITLE was the best window", "app_view_title_best"),
    ]
    lines += [f"| {label} | {rate(loc, k):.1%} | {rate(loc_tr, k):.1%} |" for label, k in loc_rows]
    return "\n".join(lines) + "\n", summary


# --------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit-claims", type=int, default=0, help="per split, for a quick smoke run")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console

    t0 = time.perf_counter()
    sentences, claims = load_release()
    if args.limit_claims:
        claims = {k: v[: args.limit_claims] for k, v in claims.items()}
    beir = load_beir()
    chunk_text = {d.doc_id: _doc_text(d.title, d.text) for d in beir.corpus}
    beir_text = {d.doc_id: d.text for d in beir.corpus}
    beir_title = {d.doc_id: d.title for d in beir.corpus}
    index = InvertedIndex.build(
        [SparseDoc(doc_id=d.doc_id, zones={"title": d.title, "body": d.text}) for d in beir.corpus]
    )
    print(f"[index] {len(beir.corpus)} docs, vocab {index.vocabulary_size()}")

    pairs = build_pairs(claims, index, sorted(chunk_text))
    print(f"[pairs] {len(pairs)}")

    model = "text-embedding-3-small"
    doc_cache = _load_cache("dense_cache", model)
    if not doc_cache:
        raise SystemExit(
            "dense doc cache missing — run `python -m eval.run_ablation --methods dense` first"
        )
    # dev claim text == BEIR query text (verified); key the query cache by text
    claim_vecs, chars = claim_vectors([strip_markers(p.claim) for p in pairs], model, beir.queries)

    lexical_fn = lambda a, b: tfidf_cosine(index, a, b)  # noqa: E731 — same as the app
    for p in pairs:
        chunk = chunk_text[p.doc_id]
        key = strip_markers(p.claim)
        block = ContextBlock(
            index=1,
            document_id=uuid.UUID(int=0),
            chunk_id=uuid.UUID(int=0),
            char_start=0,
            char_end=len(chunk),
            content=chunk,
        )
        vectors = {key: claim_vecs[key].tolist(), chunk: doc_cache[p.doc_id].tolist()}
        (check,) = score_claims(
            [(p.claim, [1], False)],
            [block],
            lexical_fn=lexical_fn,
            vectors=vectors,
            threshold=APP_THRESHOLD,
        )
        p.lexical, p.semantic, p.combined, p.status = (
            check.lexical,
            check.semantic,
            check.score,
            check.status,
        )
        if p.kind == "support":
            # primary view: windows over the abstract text only (gold indices refer to it);
            # app view: the full chunk incl. the title, exactly what the checker maxes over
            abstract_only = localize(p, beir_text[p.doc_id], sentences[p.doc_id], lexical_fn)
            app_view = localize(p, chunk, sentences[p.doc_id], lexical_fn)
            if abstract_only and app_view:
                span = app_view["best_span"]
                title_len = len(_norm(beir_title[p.doc_id]))
                p.loc = abstract_only | {
                    "app_view_best_hit": app_view["best_window_hit"],
                    "app_view_title_best": span is not None and span[0] < title_len,
                }

    report, summary = evaluate(pairs)
    elapsed = time.perf_counter() - t0
    report += (
        f"\n_Run: {len(pairs)} pairs in {elapsed:.0f}s; embedding cost this run: "
        f"{chars:,} claim chars (~{chars // 4:,} tokens) — abstracts and dev claims "
        "reused from the ablation's cache._\n"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = RESULTS_DIR / "citation_check_eval.md"
    if out_md.exists():
        # keep a hand-written interpretation section (everything after the marker) across reruns
        old = out_md.read_text(encoding="utf-8")
        marker = "\n## Interpretation"
        if marker in old:
            report += old[old.index(marker) :]
    out_md.write_text(report, encoding="utf-8")
    (RESULTS_DIR / "citation_check_pairs.json").write_text(
        json.dumps(
            {
                "threshold": APP_THRESHOLD,
                "summary": summary,
                "pairs": [
                    {
                        "split": p.split,
                        "kind": p.kind,
                        "claim_id": p.claim_id,
                        "doc_id": p.doc_id,
                        "lexical": p.lexical,
                        "semantic": p.semantic,
                        "combined": p.combined,
                        "status": p.status,
                        "gold_sentences": p.gold_sentences,
                        "localization": p.loc or None,
                    }
                    for p in pairs
                ],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(report)


if __name__ == "__main__":
    main()
