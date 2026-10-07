# Keystone: trustworthy RAG over your own documents

**CSD358 IR Hackathon, Track T1: Retrieval-Augmented Generation and trustworthy answers.**

Keystone is a NotebookLM-style app. You upload PDFs into a notebook, ask questions, and get
answers in which every sentence is linked to a ranked source chunk (with its page number) and
checked against that chunk. The retriever is a real, inspectable IR component: a
**from-scratch positional, zoned inverted index** (Porter stemming, SMART `lnc.ltc` tf-idf,
Okapi BM25, champion lists, index elimination, heap top-K, Boolean and phrase queries). It is
fused with dense embedding search through Reciprocal Rank Fusion, and every step can be
inspected in the UI.

- Report: [`docs/report/report.pdf`](docs/report/report.pdf) (HTML source alongside it)
- Team: Rahul Vaidhya (2410110259), Akshat Bansal (2410110039), Ananmay Dubey (2410110513), Yug Gupta (2410110490)
- Demo video: _add your unlisted YouTube/Drive link here_
- Demo video script (verified queries and expected results): [`docs/video-script.md`](docs/video-script.md)

---

## What works (verified end to end on 2026-10-07)

| Feature | Where | IR concepts |
|---|---|---|
| Upload PDF → parse → section/chunk → embed → READY (background worker) | `backend/app/services/ingestion/` | "what is a document" (1 000-char chunks inside page/section boundaries), dedupe by checksum |
| **Search page: Ranked / Boolean / Phrase** with full query-processing traces (tokens → case-folding → stop words → stems → postings, df/idf, per-term score contributions) | `backend/app/services/retrieval/sparse/`, `frontend/src/pages/SearchPage.tsx` | inverted index, positional index, postings intersection in increasing-df order, AND/OR/NOT, phrase queries, tf-idf vs BM25, zones (heading vs body), champion lists, idf-threshold index elimination, heap top-K |
| **Search page: Semantic (hybrid)** — each result shows the fused RRF score that orders it, plus its dense rank + cosine distance and BM25 rank + score | `retrieval/fusion.py`, `SearchPage.tsx` | dense retrieval, BM25, Reciprocal Rank Fusion |
| **Chat with cited answers** (streaming) | `backend/app/services/chat/service.py` | hybrid retrieval = dense kNN (pgvector) + from-scratch BM25, fused with RRF; top-k context blocks numbered `[n]` |
| **Per-sentence citation checker** (supported / weak / uncited + scores) | `backend/app/services/chat/citation_check.py` | tf-idf `ltc` cosine of the sentence vs. best window of the cited chunk + embedding cosine |
| Page-accurate citations (click a `[n]` to open the source passage) | `backend/app/services/ingestion/search.py` | per-page markers kept through chunking |
| Broad questions ("summarize these chapters") → map-reduce over section summaries | `backend/app/services/chat/broad_query.py`, `retrieval/mapreduce.py` | query classification + per-section retrieval |
| Refuses when the sources don't contain the answer | `chat/service.py` (`_SYSTEM_PROMPT`) | grounding |
| Notebook overview, chat history, admin debug trace (exact hits + prompt), thumbs feedback | `chat/`, `knowledge/` | |
| Multi-tenant orgs, roles, notebook sharing, folder access roles, embeddable chat widget | `auth`, `access_roles`, `embed` | |

Evaluation (see `backend/eval/results/`):

- **SciFact (BEIR), 300 queries:** nDCG@10 tf-idf 0.619 → BM25 0.686 → dense 0.717 → hybrid RRF **0.735**.
- **Hand-judged textbook queries (18):** hybrid MRR@10 **0.935**, P@5 0.633, a relevant page in the
  top 5 for every query. The local text-layer parser raised MRR@10 for every method compared with
  the hosted parser (BM25 0.798 → 0.909).
- **Citation checker on SciFact claims:** ROC-AUC 0.999 against off-topic citations, 0.808 against on-topic wrong citations.

## What is still planned / known limitations

- The citation checker measures topical support, **not entailment**: 89% of SciFact claims that
  contradict their source are still marked "supported". An NLI model is the next step.
- The sparse channel has no spelling correction (a typo such as "ekamn" only matches "transport").
  The dense channel covers this today. k-gram / edit-distance correction (IIR ch. 3) is planned.
- Section summaries for broad questions are produced ~1 min **after** a document turns READY,
  so broad questions asked immediately fall back to normal retrieval.
- Broad-query answers carry section-level citations without per-sentence claim checks.
- Open security items from QA (chat history visible to all notebook members, etc.) are listed in
  `.claude/known-issues.md` (S1–S4).

---

## Setup (Windows, tested; macOS/Linux use the commented commands)

### Prerequisites

- **Docker Desktop**, running (Postgres + pgvector and Redis run in containers). If `docker ps`
  says it cannot find `dockerDesktopLinuxEngine`, Docker Desktop isn't started.
- **Python 3.12+** (tested on 3.12 and 3.14), **Node 18+** (tested on 24).
- An **OpenRouter API key** for real answers/embeddings (the app also runs fully offline on
  fake models; see "Seam modes").

### 1. Infra

```bash
docker compose up -d postgres redis      # Postgres on host port 55432, Redis on 6379
                                         # (the database/user are still called "veratas", the project's earlier name)
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate                   # Git Bash/macOS/Linux: source .venv/Scripts/activate (or .venv/bin/activate)
pip install -e ".[dev,real]"             # add ",ireval" to run the SciFact evaluation
copy .env.example .env                   # then edit .env (see below)
alembic upgrade head                     # current head: 0025
```

> **Do not copy a `.venv` folder from another computer.** A venv stores the absolute path of the
> Python that created it (`No Python at 'C:\Users\<someone-else>\...'`). Delete it and recreate it.

Recommended `.env` for the full demo (real models, all IR features on):

```ini
PARSER_MODE=real
EMBEDDER_MODE=real
LLM_MODE=real
OPENAI_API_KEY=<your OpenRouter key>       # embeddings + chat go through OpenRouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=<same key>              # only used for scanned PDFs (OCR fallback)
STORAGE_MODE=local
HYBRID_SEARCH_ENABLED=true
SPARSE_RETRIEVAL_MODE=bm25
CITATION_CHECK_ENABLED=true
ENRICHMENT_ENABLED=true
BROAD_QUERY_ENABLED=true
NOTEBOOK_OVERVIEW_ENABLED=true
SEMANTIC_OUTLINE_ENABLED=true
```

### 3. Run (three terminals, all from `backend/` with the venv active, except the frontend)

| # | What | Command |
|---|---|---|
| 1 | API | `uvicorn main:app --host 127.0.0.1 --port 8010` |
| 2 | Background worker (**required**, otherwise documents never leave `UPLOADED`) | `arq worker.WorkerSettings` |
| 3 | Frontend | `cd frontend && npm install && npm run dev` → open http://localhost:5173 |

Sign up (this creates an organization; you are its owner), create a notebook, upload PDFs,
wait for **READY**, then use **Chat** and **Search**. Restart the API and the worker after any
backend code or `.env` change (no hot reload).

### Seam modes: fake vs. real

`PARSER_MODE` / `EMBEDDER_MODE` / `LLM_MODE` = `fake | real`. `fake` needs no keys (the test
suite always uses it): the whole app works, but answers are templated and embeddings are
hash-based. With `PARSER_MODE=real` the PDF's own text layer is read locally with `pypdf`; only
PDFs with no text layer (scans) are sent to OpenRouter's parser/OCR.

---

## Data

- **Demo corpus:** H. Goosse et al., *Introduction to climate dynamics and climate modelling*
  (online textbook, climate.be/textbook), Chapter 1 (24 pages) and Chapter 2 (33 pages), plus one
  course question paper. Put the chapter PDFs in `backend/eval/data/textbook/` (gitignored,
  not redistributed) to reproduce the textbook evaluation.
- **SciFact** (Wadden et al., 2020) via BEIR (Thakur et al., 2021): 5 183 abstracts and 300 test
  queries with relevance judgments. Downloaded automatically by `eval/scifact.py`.
- No personal data is collected. No crawling is involved.

## Reproducing the evaluation

From `backend/` with the venv active:

```bash
# SciFact ablation: tf-idf / BM25 / zones / champions / dense / hybrid (one-time ~2M embedding tokens)
python -m eval.run_ablation --k 10 --methods tfidf bm25 bm25_zones bm25_champions dense hybrid_rrf
# Citation-checker accuracy on SciFact claims
python -m eval.citation_check_eval
# Hand-judged textbook queries against the live app (API + worker running)
python -m eval.textbook_eval --setup --pdf-dir eval/data/textbook      # prints --email/--password/--notebook
python -m eval.textbook_eval --email ... --password ... --notebook ...  [--dense-api http://127.0.0.1:8011]
```

For the `dense` row, start a second API with hybrid search off:
`HYBRID_SEARCH_ENABLED=false uvicorn main:app --port 8011` (Git Bash; in PowerShell set
`$env:HYBRID_SEARCH_ENABLED="false"` first). Results are written to `backend/eval/results/`.

## Tests

```bash
# backend: 496 tests on a throwaway Testcontainers Postgres (Docker must be running)
cd backend && pytest -m "not real_parser and not hierarchical_eval and not eval"
# frontend: 225 tests, plus type-check + production build
cd frontend && npm run test && npm run build
```

The tests always run on fake models with every feature flag at its default:
`tests/conftest.py` sets `KEYSTONE_IGNORE_DOTENV=1`, so your demo `.env` is ignored during
tests and doesn't need to be moved aside.

---

## Troubleshooting

- **Chat shows "The AI model provider request failed…"**: OpenRouter rejected the call (invalid
  or expired key, or no credits). Fix `OPENAI_API_KEY` in `backend/.env` and restart the API.
  The full error is in the API log (`chat.stream_failed`).
- **Chat shows "Notebook not found"**: the notebook was deleted or belongs to another org/user.
- **"I don't have that in the provided sources."** is the intended refusal when retrieval finds no
  support. Check that the documents are READY and use the **Search** page to see what the
  index actually contains for your terms.
- **Document stuck at `UPLOADED`/`PARSING`**: the worker isn't running.
- **Frontend can't reach the API**: the backend must be on port **8010** (`frontend/vite.config.ts`).

## Project layout

```
backend/
  main.py  worker.py                 # uvicorn main:app / arq worker.WorkerSettings
  app/services/retrieval/sparse/     # from-scratch IR core: text.py index.py scoring.py trace.py
  app/services/retrieval/            # service.py (hybrid), fusion.py (RRF), mapreduce.py
  app/services/chat/                 # service.py (RAG), citation_check.py, broad_query.py
  app/services/ingestion/            # parsing, structuring (chunking), embedding, enrichment
  app/services/seams/                # parser / embedder / LLM adapters (fake + real)
  eval/                              # SciFact ablation, citation-checker eval, textbook eval
  migrations/  tests/
frontend/src/                        # React SPA: pages/ components/ services/
docs/report/                         # assignment report (HTML + PDF)
.claude/                             # build notes, known-issues.md, memory
```
