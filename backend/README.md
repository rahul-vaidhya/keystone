# Veratas — Backend

FastAPI + arq workers + Postgres/pgvector. Modular monolith. See `../.claude/context/`.

## Layout

```
backend/
  app/
    platform/   # config, db Base/engine/sessionmaker (tenant_session arrives in F02)
    identity/   # orgs, users (auth arrives in Phase 1)
    documents/ ingestion/ knowledge/ retrieval/ chat/   # feature modules (built in later phases)
  migrations/   # alembic (env.py is async; baseline = extensions + organizations + users)
  tests/        # smoke tests
  main.py       # FastAPI entrypoint  (uvicorn main:app)
  worker.py     # arq entrypoint      (arq worker.WorkerSettings)
```

## Local dev

```bash
# 1. infra (from repo root) — Postgres on host port 55432, Redis on 6379
docker compose up -d

# 2. python env (from backend/)
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env

# 3. migrate
alembic upgrade head

# 4. run the API — use 8010, not 8000 (see note below)
uvicorn main:app --host 127.0.0.1 --port 8010

# 5. tests
pytest
```

### Port notes (Windows)

- **Postgres host port is 55432, not 5432.** A native Windows Postgres service often
  already owns 5432; `docker-compose.yml` maps the container's 5432 to host 55432 to
  avoid that collision without requiring you to stop the Windows service.
- **Backend runs on 8010, not 8000.** Port 8000 is occasionally already bound by an
  unrelated process on this machine (not part of this repo). 8010 is the value
  `frontend/vite.config.ts`'s dev proxy targets — keep both in sync if you change it.
- `uvicorn --reload` can fail with multiprocessing/named-pipe errors in some sandboxed
  shells on Windows; drop `--reload` if you hit that, or run it from a normal terminal.

## Status

Phase 0: **F00 (layout) + F01 (DB + migrations) done.** Next: F02 tenant isolation,
F03 seams + fakes, F04 CI against Testcontainers.
