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
- [ ] F02 Tenant isolation (app-level scoping + tenant_session; RLS written but flag-OFF) — app-level isolation test passing
- [ ] F03 Seams + fakes
- [ ] F04 CI against Testcontainers Postgres

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
Phase: **0 — in progress.** F00 + F01 done (9 smoke tests green, ruff clean). Next action:
**F02 Tenant isolation** — `tenant_session(org_id)` helper (request + worker), base-repository
app-level `org_id` scoping, RLS policies + `app_user`/`migrator` split written flag-OFF, two-org
isolation integration test.
