"""Hand-judged retrieval evaluation on the app's own corpus (climate textbook ch. 1-2).

Runs against a LIVE API (real parser/embedder), so it measures the deployed pipeline end
to end: parsing -> chunking -> index -> ranking. Queries + page-level judgments live in
`eval/textbook_qrels.json`. A retrieved chunk is relevant when its page span overlaps a
judged page of the right chapter (chapters are told apart by page count: ch1 = 24 pages,
ch2 = 33 pages, so upload file names do not matter).

Methods:
  tfidf   /retrieval/sparse-search, SMART lnc.ltc (from-scratch index)
  bm25    /retrieval/sparse-search, Okapi BM25 (from-scratch index)
  hybrid  /retrieval/search on --api (production: dense kNN + BM25 fused with RRF)
  dense   /retrieval/search on --dense-api (an API started with HYBRID_SEARCH_ENABLED=false)

Run from `backend/` (API + worker running):
    # 1. one-time: new account + notebook, uploads the two chapter PDFs, waits for READY
    .venv/Scripts/python -m eval.textbook_eval --setup --pdf-dir eval/data/textbook
    # 2. evaluate (prints + writes eval/results/textbook_eval.md)
    .venv/Scripts/python -m eval.textbook_eval --email E --password P --notebook NB \
        [--dense-api http://127.0.0.1:8011] [--label pypdf]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CHAPTER_BY_PAGES = {24: "1", 33: "2"}
K = 10


def login(api: str, email: str, password: str) -> dict[str, str]:
    r = httpx.post(f"{api}/auth/login", json={"email": email, "password": password}, timeout=60)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def setup(api: str, pdf_dir: Path) -> None:
    c = httpx.Client(base_url=api, timeout=600)
    email, password = f"eval+{int(time.time())}@example.com", "EvalPass!2026"
    r = c.post(
        "/auth/signup", json={"email": email, "password": password, "org_name": "Textbook eval"}
    )
    r.raise_for_status()
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    nb = c.post("/notebooks", headers=h, json={"name": "Climate textbook (eval)"}).json()["id"]
    ids = []
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        d = c.post(
            "/documents/upload",
            headers=h,
            files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
        ).json()
        ids.append(d["id"])
        c.post(f"/notebooks/{nb}/documents/{d['id']}", headers=h).raise_for_status()
    while True:
        docs = {d["id"]: d["status"] for d in c.get("/documents", headers=h).json()}
        if all(docs[i] in ("READY", "FAILED") for i in ids):
            break
        time.sleep(5)
    print(f"statuses: {[docs[i] for i in ids]}")
    print(f"--email {email} --password {password} --notebook {nb}")


def chapter_map(api: str, h: dict[str, str], notebook: str) -> dict[str, str]:
    docs = httpx.get(f"{api}/notebooks/{notebook}/documents", headers=h, timeout=60).json()
    return {d["id"]: CHAPTER_BY_PAGES.get(d.get("page_count") or 0, "?") for d in docs}


def retrieve(method: str, args: argparse.Namespace, h: dict, query: str) -> list[dict]:
    if method in ("tfidf", "bm25"):
        body = {"notebook_id": args.notebook, "query": query, "scheme": method, "k": K}
        r = httpx.post(f"{args.api}/retrieval/sparse-search", headers=h, json=body, timeout=120)
    else:
        api = args.api if method == "hybrid" else args.dense_api
        body = {"notebook_id": args.notebook, "query": query, "k": K}
        r = httpx.post(f"{api}/retrieval/search", headers=h, json=body, timeout=120)
    r.raise_for_status()
    return r.json()["results"]


def judge(hit: dict, chapters: dict[str, str], relevant: dict[str, list[int]]) -> set:
    """(chapter, page) pairs this hit covers that are judged relevant (empty = not relevant)."""
    ch = chapters.get(str(hit["document_id"]), "?")
    lo, hi = hit.get("page_start"), hit.get("page_end")
    if lo is None or hi is None:
        return set()
    return {(ch, p) for p in range(lo, hi + 1) if p in relevant.get(ch, [])}


def score(hits: list[dict], chapters: dict, relevant: dict) -> dict[str, float]:
    covered = [judge(h, chapters, relevant) for h in hits[:K]]
    rel = [bool(c) for c in covered]
    n_rel_pages = sum(len(v) for v in relevant.values())
    first = next((i for i, r in enumerate(rel, start=1) if r), None)
    return {
        "P@5": sum(rel[:5]) / 5,
        "P@10": sum(rel[:10]) / 10,
        "R@10": len(set().union(*covered)) / n_rel_pages if covered else 0.0,
        "Hit@5": float(any(rel[:5])),
        "MRR@10": 1 / first if first else 0.0,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8010")
    p.add_argument("--dense-api", default=None)
    p.add_argument("--setup", action="store_true")
    p.add_argument("--pdf-dir", type=Path, default=HERE / "data" / "textbook")
    p.add_argument("--email")
    p.add_argument("--password")
    p.add_argument("--notebook")
    p.add_argument("--label", default="")
    args = p.parse_args()
    if args.setup:
        setup(args.api, args.pdf_dir)
        return

    h = login(args.api, args.email, args.password)
    chapters = chapter_map(args.api, h, args.notebook)
    qrels = json.loads((HERE / "textbook_qrels.json").read_text(encoding="utf-8"))["queries"]
    methods = ["tfidf", "bm25", "hybrid"] + (["dense"] if args.dense_api else [])
    per_query: dict[str, dict[str, dict[str, float]]] = {m: {} for m in methods}
    for m in methods:
        for q in qrels:
            per_query[m][q["id"]] = score(retrieve(m, args, h, q["query"]), chapters, q["relevant"])

    metrics = ["P@5", "P@10", "R@10", "Hit@5", "MRR@10"]
    title = f"Textbook retrieval eval{f' ({args.label})' if args.label else ''}"
    lines = [
        f"# {title}",
        "",
        f"{len(qrels)} hand-judged queries, page-level relevance, top-{K} chunks per method. "
        "Generated by `python -m eval.textbook_eval`.",
        "",
        "| method | " + " | ".join(metrics) + " |",
        "|---|" + "---|" * len(metrics),
    ]
    for m in methods:
        vals = [statistics.mean(per_query[m][q["id"]][x] for q in qrels) for x in metrics]
        lines.append(f"| {m} | " + " | ".join(f"{v:.3f}" for v in vals) + " |")
    lines += ["", "## MRR@10 by query kind", "", "| kind | n | " + " | ".join(methods) + " |"]
    lines.append("|---|---|" + "---|" * len(methods))
    for kind in sorted({q["kind"] for q in qrels}):
        qs = [q for q in qrels if q["kind"] == kind]
        vals = [statistics.mean(per_query[m][q["id"]]["MRR@10"] for q in qs) for m in methods]
        lines.append(f"| {kind} | {len(qs)} | " + " | ".join(f"{v:.3f}" for v in vals) + " |")
    lines += ["", "## Per query (MRR@10)", "", "| id | kind | query | " + " | ".join(methods)]
    lines[-1] += " |"
    lines.append("|---|---|---|" + "---|" * len(methods))
    for q in qrels:
        vals = " | ".join(f"{per_query[m][q['id']]['MRR@10']:.2f}" for m in methods)
        lines.append(f"| {q['id']} | {q['kind']} | {q['query']} | {vals} |")
    report = "\n".join(lines) + "\n"
    print(report)
    suffix = f"_{args.label}" if args.label else ""
    (HERE / "results" / f"textbook_eval{suffix}.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
