# progresstracker.md — Living State

Updated by the **Remember** skill after every successful Review. Tick a box only when its
Definition of Done (see `buildplan.md`) is met. Add the commit ref next to completed items.

## Foundation
- [x] Foundation review + resolution pass (context docs only; 2026-06-21) — C1–C3, M1–M5, L1–L6 resolved.

## Phase 0 — Platform skeleton
- [x] F00 Repo + layout (uncommitted; no git) — modular `backend/app/*` layout, pydantic-settings
      config (RLS_ENABLED off, DATABASE_URL/REDIS/R2/SEAMS_MODE), structlog, docker-compose
      (pg+pgvector / redis), `frontend/` stub. DoD met: app imports; `GET /health`→200; config loads.
- [x] F01 DB + migrations (uncommitted; no git) — async SQLAlchemy `Base`/engine, async Alembic env,
      baseline migration (vector+citext extensions; organizations+users, org_id everywhere). DoD met:
      migration applies on a real pgvector container; smoke test inserts/reads an org.
- [x] F02 Tenant isolation (0c8bd12) — `tenant_session(org_id)` (requests + workers, `set_config`
      transaction-local, gated by RLS_ENABLED), `BaseRepository._scoped()` always-on org_id filter,
      `TenantContext`, migration 0002 (tenant_isolation policies + app_user/migrator split + FORCE RLS,
      written but flag-OFF). DoD met: app-level two-org isolation test proves zero cross-read on a real
      pgvector container. Also cleared 3 F00–F01 review minors (env.py model imports, worker on_startup
      logging, db.py shadow comment). 15 tests green, ruff clean.
- [x] F03 Seams + fakes (c35ee11) — `platform/seams.py`: `Parser`/`Embedder`/`LLM` runtime_checkable
      Protocols + shared types (`ParsedDoc`/`OutlineNode`/`Message`). Fakes default everywhere
      (`FakeEmbedder` deterministic unit vector from sha256; `FakeLLM` templated cited answer;
      `FakeParser` fixed text + 2-node outline). Real adapters behind the seam: `RealEmbedder`/`RealLLM`
      OpenAI-compatible (lazy SDK import, config-gated, `SeamNotConfigured`); `RealParser` = Phase-2 (F20)
      stub (OCR vendor deferred). `get_parser/get_embedder/get_llm` factory on `SEAMS_MODE`. DoD met:
      12 unit tests, no DB/keys; whole suite runs on fakes.
- [x] F04 CI against Testcontainers Postgres (c35ee11) — `.github/workflows/ci.yml`: ruff (check+format)
      + pytest on a real pgvector Testcontainers container, push/PR, `TESTCONTAINERS_RYUK_DISABLED=true`,
      seams on fakes. DoD: pipeline runs the full suite (verified green locally — 27 tests, ruff clean;
      GH Actions green on first push pending).

## Phase 1 — Identity + Documents
- [ ] F10 Auth + org creation + invites
- [ ] F11 Folders + tags
- [ ] F12 Upload + checksum dedupe

## Phase 2 — Ingestion core path
- [ ] F20 parsing stage (artifacts persisted, failures recorded)
- [ ] F21 structuring stage (sections tree + chunks + offsets, idempotent)
- [ ] F22 embedding stage (chunk embeddings, idempotent, status→ready)

## Phase 3 — Knowledge + Retrieval
- [ ] F30 Notebooks (reference join)
- [ ] F31 Flat retrieval (scoped, isolation test passing)

## Phase 4 — Chat
- [ ] F40 Grounded generation (SSE, refuses outside sources)
- [ ] F41 Citations (offset mapping + persisted)
- [ ] F42 Admin debug bundle

## Phase 5 — Frontend SPA
- [ ] F50 App shell + auth UI
- [ ] F51 Repository UI (folders/tags/upload + status)
- [ ] F52 Notebook + chat UI (streaming + citations)

## Phase 6 — Security Hardening (after MVP validated, before real customer data)
- [ ] F60 Enforced RLS (RLS_ENABLED on; app_user/migrator split; FORCE RLS; teeth-having isolation test)

---
**Demoable milestone reached:** [ ] end of Phase 4

## Current status
Phase: **0 COMPLETE.** F00–F04 done (F03+F04 = c35ee11; 27 tests green on real pgvector, ruff clean).
Next action: **Phase 1 — F10 Auth + org creation + invites** (roles `owner|admin|member`; short-lived
JWT access + httpOnly refresh cookie; `current_user` → `TenantContext`; `get_ctx` dependency from
librarydocs.md). First real consumer of the seams arrives in Phase 2 (ingestion).
