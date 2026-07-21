# Veratas

Private, source-grounded company knowledge base (NotebookLM-style), multi-tenant.

- **Backend:** FastAPI + async SQLAlchemy + Postgres/pgvector + arq (Redis job queue)
- **Frontend:** Vite + React SPA
- **Architecture:** Express-style MVC backend (`backend/app/{models,routes,controllers,
  services,middleware,config,utils}/`, JSON-only, no view layer) + conventional React
  SPA frontend (`frontend/src/{pages,components,layouts,services,context,hooks,types,
  styles}/`). Full detail in `.claude/context/architecture.md`.

This file gets a fresh clone running end-to-end. For how the project is *built*
(workflows, standards, buildplan), see `.claude/orchestrator.md`.

---

## Prerequisites

- Docker Desktop (Postgres+pgvector and Redis run in containers)
- Python 3.12+
- Node 18+
- (Windows) Git Bash or PowerShell

---

## 1. Start infra (Postgres + Redis)

From the repo root:

```bash
docker compose up -d
```

This starts:
- **Postgres (pgvector)** on host port **`55432`** (not 5432 — avoids colliding with a
  native Windows Postgres install), user/pass/db all `veratas`
- **Redis** on `6379`

Check both are healthy: `docker compose ps`.

---

## 2. Backend setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/Scripts/activate  # Git Bash / macOS / Linux

pip install -e ".[dev]"          # add ",real" too if you want live parser/embedder/LLM calls:
                                  # pip install -e ".[dev,real]"

copy .env.example .env           # Windows: copy ; else: cp .env.example .env
```

`.env` defaults to **fake seams** (`PARSER_MODE=EMBEDDER_MODE=LLM_MODE=fake`) and
**`STORAGE_MODE=r2`** with no credentials — see [Seam modes](#seam-modes--fake-vs-real)
below before running a real end-to-end ingestion.

### Migrate

```bash
alembic upgrade head
```

Current head: **`0018`** (`backend/migrations/versions/`). Re-run this any time you pull
new migration files.

### Run the API

```bash
uvicorn main:app --host 127.0.0.1 --port 8010
```

Real entrypoint is **`backend/main.py`** — run `uvicorn main:app`, not `app.main:app`.
Use port **`8010`**, not 8000: `frontend/vite.config.ts`'s dev proxy targets 8010 (port
8000 is occasionally already bound by an unrelated process on some dev machines).

### Run the worker (required for documents to actually finish processing)

Uploads are auto-dispatched through a background pipeline (parse → structure → embed →
optionally enrich) via arq. **Without a worker running, an uploaded document sits at
`UPLOADED`/`PARSING`/etc. forever** — the API alone will not advance it.

In a second terminal, from `backend/` (same venv):

```bash
arq worker.WorkerSettings
```

Real entrypoint is **`backend/worker.py`** — run `arq worker.WorkerSettings` from
inside `backend/`, not `app.worker:WorkerSettings`.

### Run the tests

```bash
pytest
```

Uses Testcontainers to spin up a disposable Postgres — Docker must be running. Fully
offline otherwise (seams are faked). A couple of tests are opt-in / excluded from CI
(`real_parser`, `hierarchical_eval` markers) — they need a live `OPENROUTER_API_KEY`/
`OPENAI_API_KEY` and are not part of the normal run.

---

## 3. Frontend setup

In a third terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** — sign up to create an organization (you become
**owner**), or sign in. The Vite dev server proxies `/auth`, `/documents`, `/chat`,
etc. to the backend at `127.0.0.1:8010` (see `frontend/vite.config.ts`).

### Frontend tests / build

```bash
npm run test    # vitest
npm run build   # tsc -b && vite build
```

---

## Running everything (summary)

Four things need to be running at once for a fully working local dev setup:

| # | What | Command | Where |
|---|------|---------|-------|
| 1 | Postgres + Redis | `docker compose up -d` | repo root |
| 2 | Backend API | `uvicorn main:app --host 127.0.0.1 --port 8010` | `backend/` (venv active) |
| 3 | Background worker | `arq worker.WorkerSettings` | `backend/` (venv active) |
| 4 | Frontend dev server | `npm run dev` | `frontend/` |

Skipping #3 is the single most common "why isn't my document processing" gotcha in
this project.

---

## Seam modes — fake vs. real

Three external calls (document parsing, embeddings, LLM chat) go through swappable
"seam" interfaces, each independently switched in `.env`:

```
PARSER_MODE=fake|real
EMBEDDER_MODE=fake|real
LLM_MODE=fake|real
```

`fake` (the default) needs no API keys and is what the automated test suite always
uses — good enough to exercise the whole app (upload → ingest → chat) without any
credentials, but answers/embeddings are not semantically meaningful.

To get **real** parsing/embeddings/chat answers in local dev, set in `.env`:

```
PARSER_MODE=real
EMBEDDER_MODE=real
LLM_MODE=real

OPENROUTER_API_KEY=<your key>          # powers the real parser
OPENAI_API_KEY=<same OpenRouter key>   # powers embedder + LLM (OpenAI-compatible route)
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

One OpenRouter key feeds all three real seams (parser via `OPENROUTER_API_KEY`,
embedder/LLM via `OPENAI_API_KEY`/`OPENAI_BASE_URL` pointed at OpenRouter). You'll also
need `pip install -e ".[dev,real]"` (installs `openai` + `pypdf`) if you didn't already.

### Object storage

```
STORAGE_MODE=r2|local
```

Defaults to `r2` (Cloudflare R2 / S3-compatible), which needs `R2_*` credentials in
`.env`. For a fully offline local run with no cloud account, set `STORAGE_MODE=local`
— documents are stored under `backend/.localstorage/` instead. Both the API process and
the worker must agree on this setting (it's read from `.env`, not overridable per
request), since they share the same blobs.

---

## Project layout

```
veratas_project/
  docker-compose.yml       # Postgres+pgvector, Redis
  backend/
    main.py                # FastAPI entrypoint    → uvicorn main:app
    worker.py               # arq entrypoint         → arq worker.WorkerSettings
    app/
      models/ routes/ controllers/ services/ middleware/ config/ utils/
    migrations/             # Alembic (head: 0018)
    tests/
  frontend/
    src/
      pages/ components/ layouts/ services/ context/ hooks/ types/ styles/
  .claude/
    orchestrator.md          # how this project is built — read this if contributing
    context/                 # architecture, code standards, buildplan context
    memory.md                # session history / decisions
    progresstracker.md        # what's done, what's next
```

---

## Troubleshooting

- **Document stuck at `UPLOADED`/`PARSING`/etc.** — the arq worker isn't running (or
  died). Start it: `arq worker.WorkerSettings` from `backend/`.
- **Upload/parse fails with `STORAGE_MODE`/R2 errors** — no R2 credentials in dev. Set
  `STORAGE_MODE=local` in `.env` and restart both the API and the worker.
- **Port 8000 already in use / frontend can't reach the API** — this project runs the
  backend on **8010**, not 8000. Confirm `frontend/vite.config.ts`'s proxy target
  matches whatever port you actually started uvicorn on.
- **Backend code edited but behavior didn't change** — `uvicorn`/`arq` don't hot-reload
  by default here; restart both processes after backend changes.
- **Migrations out of date** — re-run `alembic upgrade head` any time you pull; check
  current state with `alembic current`.
- **Chat/parsing "works" but answers are meaningless** — you're on `PARSER_MODE=
  EMBEDDER_MODE=LLM_MODE=fake` (the default). See [Seam modes](#seam-modes--fake-vs-real).
