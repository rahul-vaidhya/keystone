# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

## Current phase
**Phase 0 in progress.** F00 (repo+layout) + F01 (DB+migrations) + F02 (tenant isolation) built &
green (F02 committed 0c8bd12, 2026-06-22; 15 tests). Next: **F03 Seams + fakes**. See
`buildplan.md`/`progresstracker.md`.

## Phase 0 build decisions (F00/F01, 2026-06-22)
- **Baseline migration = minimum**: `vector`+`citext` extensions + `organizations`+`users` only.
  Feature tables land with their features (Phases 1–2), not in a giant dead baseline.
- **ORM models per-module** (`identity/models.py`) on one shared `Base` in `platform/db.py`;
  Alembic `target_metadata = Base.metadata`, env.py imports each module's models for side effects.
- **Async stack**: `postgresql+asyncpg` DSN; async Alembic env (`async_engine_from_config` +
  `connection.run_sync`). UUID PKs default `gen_random_uuid()` (core in pg16, no pgcrypto needed).
- **Seam adapters NOT built yet** (deferred to F03 per "stop after F01"); `SEAMS_MODE=fake` config
  field exists. `tenant_session` + base-repo scoping built in F02 (0c8bd12). `frontend/` is a
  README-only stub until F50.
- **Tests**: pure smoke tests run anywhere; DB smoke uses a Testcontainers pgvector container +
  the REAL migration (no mocked DB). Layout: `backend/` (pyproject, hatchling pkg=`app`, pytest
  `pythonpath=["."]`), entrypoints `backend/main.py` + `backend/worker.py`.

## Phase 0 build decisions (F02, 2026-06-22, 0c8bd12)
- **`tenant_session(org_id)`** in `platform/db.py` (request + worker); base-repo `org_id` filter in
  `platform/repository.py` (`BaseRepository[ModelT]._scoped()`, PEP-695 generic); `TenantContext` in
  `platform/context.py`; first concrete scoped repo = `identity/repository.py:UserRepository` (F10 extends).
- **Migration 0002 is flag-gated at apply time**: `upgrade()`/`downgrade()` read `settings.RLS_ENABLED`
  and **return early when OFF** (MVP default) → the tenant_isolation policies + app_user/migrator role
  split + FORCE RLS are WRITTEN but inert. F60 flips the flag and ships the real enabling migration.
  Pattern: each future tenant table adds its policy here, keyed on `org_id` (on `id` for `organizations`).
- **NO feature tables created in F02** (document_tags/knowledge_base_documents/messages/message_traces
  don't exist yet — they land with their features, locked decision). The org_id-everywhere rule is
  honored via a metadata-guard test (`test_metadata_smoke.py`) + the 0002 RLS pattern, not by building
  Phase 1/2/4 tables now.
- **Testing tenant_session against the container**: db.py builds its engine/sessionmaker at import
  against the dev URL, so a conftest `tenant_engine` fixture **rebinds `app.platform.db.engine` +
  `.sessionmaker`** to the Testcontainers URL (restored after) so the REAL helper is exercised. DoD
  isolation test seeds both orgs unscoped, reads via the scoped repo, and a control unscoped `select`
  proves both orgs' rows coexist (so it's the filter isolating, not absent data). 15 tests green.

## Foundation-review resolutions (2026-06-21 — applied to context docs, no code)
- **Tenancy split (the key call):** *Schema + plumbing done NOW; enforced RLS + restricted DB role
  DEFERRED to Phase 6 hardening, gated by `RLS_ENABLED` (default OFF in dev/test). App-level `org_id`
  scoping is ALWAYS on.* Resolves C2/C3.
  - Every tenant-scoped table carries `org_id` — incl. join/child tables (`document_tags`,
    `knowledge_base_documents`, `messages`, `message_traces`). No scope-via-parent. (C1)
  - One `tenant_session(org_id)` helper, `set_config('app.org_id', :org, true)` (transaction-local,
    no pooled-connection leak — see gotcha below re: why NOT `SET LOCAL`), used by BOTH
    requests AND arq workers (worker reads org_id from the job payload). (C3)
  - RLS predicate: `organizations` keys on `id`; others on `org_id`; both use `current_setting(
    'app.org_id', true)` (missing_ok → unset GUC = no rows). `app_user` vs `migrator` role split,
    `FORCE RLS` — all Phase 6 (F60). (L1)
- **One authoritative status enum** in `documents/status.py`: `UPLOADED→PARSING→STRUCTURING→
  EMBEDDING→READY (+FAILED)`. Killed the stray `parsed` state. (M1)
- **F42 debug bundle persisted** to new `message_traces` table (admin-read-only), not recomputed. (M3)
- **Deletes:** DB children via `ON DELETE CASCADE` in-tx; object-store blobs via idempotent
  delete job + periodic orphan sweep. No "same DB tx" for blobs. (M4)
- **Retrieval** filters `model = :active_model` (no duplicate hits on re-embed). Filtered-ANN: exact
  KNN for small scope, HNSW+raised ef_search for large; recall tuning = V2 revisit. (M2, L6)
- **Auth:** roles `owner|admin|member` (creator→owner, invite→member); session = short-lived JWT
  access + httpOnly refresh cookie. (M5)
- **Misc:** LLM seam → `async def stream`; `ParsedDoc` gains `language` (Parser returns it);
  degenerate-outline → one root section spanning full range so every chunk has a `section_id`. (L2/L3/L5)

## The embedding-dimension asterisk (only exception to the additive V2/V3 promise)
SAME-dimension model swap is free (still `vector(1536)`, new rows under the new model name). A
DIFFERENT-dimension model requires a MIGRATION (separate vector(N) column/table per dim) — a single
fixed-width vector column cannot hold mixed dimensions. (M2)

## Locked decisions (do not relitigate without explicit reason)
- **Backend = Python** (FastAPI + async workers). The product's hard parts (ingestion, OCR,
  embeddings, RAG) live in Python's ecosystem. Node/Next.js would force the hardest work into
  the weakest tooling.
- **Frontend = Vite React SPA**, thin/presentational only. Next.js reserved for a *future*
  public marketing site + embeddable bot, never the app backbone.
- **Storage = Postgres + pgvector**, one database. Rejected MongoDB: our hot query is a vector
  search joined with relational filters; tenancy needs RLS; vectors + chunks must delete atomically.
- **Shape = modular monolith** (one deploy, hard module seams). Rejected microservices (premature).
- **Seams = exactly 3** (parser, embedder, llm) — the ones we fake in tests and will swap.
  pgvector/object-store/queue are called directly. No Protocol hierarchy, no generic DB abstraction.
- **Jobs = arq** (async, Redis-backed). Rejected Celery (heavy) for now.
- **Permissions = deferred.** MVP retrieval calls `resolve_allowed_documents(ctx)` (a function
  called INSIDE `retrieve()`), which returns all org docs in MVP. Groups/grants are designed-for but
  NOT built; the forward hook is that function, not a table or a caller-supplied parameter.
- **Eval = manual** `golden_questions.md` checklist before releases. No ragas/framework in MVP.
- **No generated TS SDK.** One hand-written typed fetch wrapper in the SPA.
- **Structural-vs-semantic split** is the spine: capture section tree / offsets / breadcrumbs now
  (free, from the parser); summaries / topics / entities later (LLM calls, flag-gated, backfillable).

## Schema future-proofing (so V2/V3 are additive)
- `sections` self-referencing tree (parent_section_id, path, offsets) — built in MVP, structural fields only.
- `chunks.section_id` links chunks into the tree now (unused by MVP retrieval).
- `embeddings` is **polymorphic** (`owner_type` chunk|section|document). MVP inserts only `chunk` rows;
  V2 inserts `section`/`document` rows into the SAME table — no migration.
- KG tables (`entities`/`mentions`/`relationships`) are designed but **created in V3**; they need only
  the provenance (chunk_id + char offsets) already stored in MVP.

## Open questions / to decide later
- Which managed parser/OCR vendor for scanned PDFs (decide in Phase 2).
- Default LLM + embedding model choice (keep behind the seam; mini-class model + text-embedding-3-small).
- When to add reranker (4th seam) — trigger is real quality complaints in V2.
- Infra providers chosen: DB = Neon (managed Postgres+pgvector, raw DATABASE_URL);
  object store = Cloudflare R2 (S3 client); auth = self-built (argon2 hash + JWT +
  current_user), NOT Supabase Auth. No database MCP in Claude Code — Alembic + repositories
  are the only DB interface (avoids bypassing the repository/migration rules).

## Gotchas learned
- **Testcontainers + Docker Desktop/Windows**: the Ryuk reaper sidecar flakes with
  "Port mapping ... port 8080 is not available", causing intermittent skips. Fix applied in
  `tests/conftest.py`: `TESTCONTAINERS_RYUK_DISABLED=true` + each fixture stops its own container
  in `finally`. Carry this env var into the F04 CI config too.
- Docker Desktop daemon must be running before DB-backed tests; the daemon needs ~30–60s after
  launch before it serves (early calls fail fast → tests skip). Poll `docker info` before running.
- **`SET LOCAL app.org_id = :org` is INVALID** — Postgres `SET`/`SET LOCAL` takes a literal token
  and rejects bind parameters, so the parameterised statement fails to parse. `tenant_session` MUST
  use `SELECT set_config('app.org_id', :org, true)` (the third arg `is_local => true` makes it
  transaction-scoped, the function equivalent of `SET LOCAL`, and accepts a bound value safely).
  Found in F02 (2026-06-22). Context docs (`architecture.md` + `librarydocs.md`) corrected — do NOT
  revert the snippet back to `SET LOCAL`.
