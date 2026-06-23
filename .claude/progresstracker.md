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
- [x] F10 Auth + org creation + invites (`8940dd1`) — `app/identity/{router,service,repository,
      models,schemas,deps,tokens,passwords,constants,exceptions}.py`, migration
      `0003_auth_password_hash`. Endpoints: signup (creates org + owner), login (multi-org aware),
      refresh, logout, `/me`, invite, list org users, patch user role. DoD met:
      `tests/test_auth.py` (34/34 suite green incl. this file).
- [x] F11 Folders + tags (`c58a5e7`) — new `app/documents` module (`Folder`/`Tag`/
      `Document`/`DocumentTag` models, repository/service/router/schemas/exceptions),
      migration `0004_folders_tags.py`. `documents` table is a minimal anchor here
      (id/org_id/folder_id/title) — F12 ALTERs it, doesn't recreate it. Folder tree via
      materialized `path`; tags get-or-create by name; tag attach/detach idempotent.
      Endpoints under `/documents/folders`, `/documents/tags`, `/documents/{id}/tags/{id}`,
      `GET /documents?folder_id=&tag_id=`. DoD met: nested folder create, tag-a-document,
      list-by-folder/tag — `tests/test_documents.py` (7 tests incl. tenant isolation).
      Folder rename/move deliberately deferred (not in DoD — see memory.md). 41/41 suite
      green, ruff clean.
- [x] F12 Upload + checksum dedupe (`0b44b9c`) — ALTERed the F11 `documents` anchor
      (migration `0005_document_upload_dedupe.py`): `storage_key/checksum/mime_type/
      byte_size/page_count/language/status/failed_stage/error_detail/metadata`,
      `unique(org_id, checksum)`. New `app/documents/status.py` (authoritative
      `DocumentStatus` enum). New `app/platform/storage.py` (`ObjectStore` Protocol +
      `R2ObjectStore`, NOT a 4th seam — object store is called directly per
      architecture.md; tests override the FastAPI dependency with an in-memory fake).
      `POST /documents/upload` (multipart) computes a sha256 checksum, checks
      `org_id+checksum` before inserting, uploads to the object store, then records the
      row; re-uploading identical bytes returns the existing document (200) instead of a
      duplicate (201 on first upload). DoD met: `tests/test_documents.py` (4 new tests:
      create+status, dedupe-returns-existing, cross-org not deduped, missing-folder 404).
      45/45 suite green, ruff clean.

## Phase 2 — Ingestion core path
- [x] F20 parsing stage (`0277cfe`) — new `app/ingestion` module (`service.py`/`router.py`
      only — no `repository.py`/`schemas.py`/`tasks.py`: no own table, no arq caller yet).
      `IngestionService.run_parsing_stage` calls `documents_service.begin_parsing` /
      `.complete_parsing` / `.fail_stage` (new methods) for every document-row mutation —
      ingestion never touches `documents.repository` directly (module boundary rule).
      Zero migration: F12 already added page_count/language/status/failed_stage/
      error_detail/metadata. `POST /ingestion/documents/{id}/parse` fetches the blob via
      `ObjectStore.get` (new method, alongside `put`), calls the `Parser` seam, persists a
      JSON artifact (text+outline) to `.../artifacts/parsing.json`, then sets
      language/page_count and status=STRUCTURING. Failures set status=FAILED+failed_stage+
      error_detail. Idempotent/resumable via a status-eligibility check in
      `DocumentRepository.begin_parsing` (UPLOADED/PARSING/FAILED-at-PARSING are eligible;
      STRUCTURING+ is a no-op). DoD met: `tests/test_ingestion.py` (successful parse,
      parser failure, idempotent re-run, tenant isolation). 49/49 suite green, ruff clean.
- [x] F21 structuring stage (`5eecac5`) — new `app/ingestion/models.py` (`Section`/`Chunk`,
      owned by ingestion since it's the producing stage) + `app/ingestion/repository.py`
      (`SectionRepository`/`ChunkRepository`: `delete_for_document`/`bulk_create` only —
      idempotency is delete+rebuild in one transaction, not row-level upsert). Migration
      `0006_sections_chunks.py`. `IngestionService.run_structuring_stage` consumes the F20
      parsing artifact, builds the sections tree from the flat outline via a level-keyed
      stack (document order preserved), implements the degenerate-outline contract (no
      headings → one root section spanning the full char range), and chunks only LEAF
      sections (~1000-char windows, break on whitespace, `token_count` heuristic
      `len(content)//4` — no tokenizer dependency) so every chunk has exactly one
      `section_id` by construction. `chunk_id` stays a deterministic
      `sha256(document_id|ordinal|content)` hash per codestandards even though delete+
      rebuild doesn't rely on it for upsert-matching. New `documents.repository`
      `begin_structuring`/`complete_structuring` + `documents.service` wrappers (mirrors
      F20's `begin_parsing`/`complete_parsing` exactly) plus a narrow
      `get_parse_artifact_key` accessor (keeps `metadata` off the public `DocumentOut`
      shape). `POST /ingestion/documents/{id}/structure`. STRUCTURING → EMBEDDING; failures
      set FAILED+failed_stage=STRUCTURING. DoD met: `tests/test_ingestion.py` (sections
      reflect the outline, chunks carry valid offsets, degenerate-outline one-root case,
      idempotent re-run, failure path, tenant isolation). 55/55 suite green, ruff clean.
- [x] F22 embedding stage (`4598698`) — new `Embedding` model in `app/ingestion/models.py` (owned by
      ingestion, same reasoning as Section/Chunk) + `EmbeddingRepository.upsert_chunk_embeddings`
      (true upsert on `unique(owner_type, owner_id, model)` via `ON CONFLICT DO UPDATE` — unlike
      F21's delete-then-rebuild, since chunk→embedding is a 1:1 keyed relationship). Migration
      `0007_embeddings.py` (`vector(1536)`, HNSW index, btree `(org_id, document_id, owner_type)`).
      `IngestionService.run_embedding_stage` reads the document's chunks (new
      `ChunkRepository.list_for_document`), calls the `Embedder` seam once in a batch, upserts,
      advances EMBEDDING → READY; failures set FAILED+failed_stage=EMBEDDING. New
      `documents.repository`/`documents.service` `begin_embedding`/`complete_embedding` pair
      (mirrors F20/F21 exactly). `POST /ingestion/documents/{id}/embed`. DoD met:
      `tests/test_ingestion.py` (status→READY with one embedding row per chunk + model/dim/
      vector-length provenance, embedder failure path, idempotent re-run, tenant isolation).
      59/59 suite green, ruff clean.

## Phase 2.5 — Real-parser validation
- [~] F23 Real parser integration — CODE + REVIEW COMPLETE, this session (commit ref: see
      memory.md). `RealParser` in `app/platform/seams.py` calls OpenRouter's file-parser
      plugin (`cloudflare-ai` first, `mistral-ocr` fallback on negligible text), recovers
      markdown heading structure into the outline (not fabricated), zero changes to
      structuring/chunking/embedding (verified via diff in review). Per-seam mode
      (`PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE`) implemented, replacing the single
      `SEAMS_MODE`. Opt-in integration test (`real_parser` marker, excluded from CI) added.
      All failure modes (API error, encrypted PDF, both engines negligible) map onto the
      existing FAILED/failed_stage model — confirmed by independent code review (zero
      violations against the 7 hard rules). 63/63 fake-only suite green, ruff clean.
      **PARTIAL only because the DoD's "one real document reaches READY on real-parser
      output" has NOT been exercised yet** — no `OPENROUTER_API_KEY`/sample PDF was
      available this session. Tick to [x] once the opt-in test has actually been run once
      against a real PDF and the heading-recovery/page-provenance findings are confirmed
      empirically (see memory.md "Next").

## Phase 3 — Knowledge + Retrieval
- [ ] F30 Notebooks (reference join)
- [ ] F31 Flat retrieval (scoped, isolation test passing)

## Phase 4 — Chat
- [ ] F40 Grounded generation (SSE, refuses outside sources)
- [ ] F41 Citations (offset mapping + persisted)
- [ ] F42 Admin debug bundle

## Phase 5 — Frontend SPA
- [x] F50 App shell + auth UI (`054aa36`) — built OUT OF SEQUENCE per direct senior instruction,
      ahead of Phase 2-4. Vite React scaffold, `App.tsx`/`ProtectedRoute`/`lib/auth.tsx`/`lib/api.ts`,
      `AppShell`/`Sidebar`/`HomePage`, `LoginPage`/`SignupPage` wired to the real F10 backend.
- [~] F51 Repository UI (folders/tags/upload + status) — PARTIAL: only the auth-adjacent slice
      (`UsersPage.tsx` org user/role management) landed alongside F50. The `DocsPage.tsx` placeholder
      was removed (it depended on the deleted `/context/docs` endpoint — see "Unplanned additions"
      in buildplan.md, resolved 2026-06-23). Folders/tags/upload itself not started — blocked on
      F11/F12. Do not resume until those land.
- [ ] F52 Notebook + chat UI (streaming + citations)

## Phase 6 — Security Hardening (after MVP validated, before real customer data)
- [ ] F60 Enforced RLS (RLS_ENABLED on; app_user/migrator split; FORCE RLS; teeth-having isolation test)

---
**Demoable milestone reached:** [ ] end of Phase 4

## Current status
Phase: **0 COMPLETE** (F00–F04, F03+F04 = c35ee11). **Phase 1 (Identity + Documents) COMPLETE**
(F10 `8940dd1`, F11 `c58a5e7`, F12 `0b44b9c`). **F50 + a slice of F51 (Phase 5 frontend) DONE
and committed (`054aa36`)**, pulled forward out of sequence per direct senior instruction.
**Phase 2 (Ingestion core path) COMPLETE: F20 parsing (`0277cfe`), F21 structuring (`5eecac5`),
F22 embedding (`4598698`).**
**Phase 2.5: F23 Real parser integration — CODE + REVIEW COMPLETE this session**, committed
separately from docs (see memory.md for refs). Marked PARTIAL in the checklist above only
because no real PDF has been run through the opt-in integration test yet (no API key/sample
available this session).
Next action: **run the opt-in `real_parser` integration test against one real PDF** (needs
`OPENROUTER_API_KEY` + a sample PDF) to empirically confirm the heading-recovery and
page-provenance findings before leaning on the real parser for Phase 3+. After that: F30
Notebooks (Phase 3), or resume the rest of F51 (folders/tags/upload UI) — ask the user which.
**Resolved (2026-06-23):** `GET /context/docs` was deleted (decision: too risky to ship,
not org-scoped) — see buildplan.md "Unplanned additions".
