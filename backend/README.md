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
# 1. infra (from repo root)
docker compose up -d

# 2. python env (from backend/)
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env

# 3. migrate
alembic upgrade head

# 4. tests
pytest
```

## Status

Phase 0: **F00 (layout) + F01 (DB + migrations) done.** Next: F02 tenant isolation,
F03 seams + fakes, F04 CI against Testcontainers.
