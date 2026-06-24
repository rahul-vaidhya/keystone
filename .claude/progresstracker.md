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
- [x] F23 Real parser integration — CODE + REVIEW + EMPIRICAL VALIDATION COMPLETE (code
      `9e7f319`, docs `90285c2`, validation run this session). `RealParser` in
      `app/platform/seams.py` calls OpenRouter's file-parser plugin (`cloudflare-ai` first,
      `mistral-ocr` fallback on negligible text), recovers markdown heading structure into
      the outline (not fabricated), zero changes to structuring/chunking/embedding (verified
      via diff in review). Per-seam mode (`PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE`)
      implemented, replacing the single `SEAMS_MODE`. Opt-in integration test (`real_parser`
      marker, excluded from CI) added. All failure modes (API error, encrypted PDF, both
      engines negligible) map onto the existing FAILED/failed_stage model — confirmed by
      independent code review (zero violations against the 7 hard rules). 63/63 fake-only
      suite green, ruff clean.
      **DoD's "one real document reaches READY on real-parser output" now met**: ran the
      opt-in test against a real 36-page PDF (`pdf/kech104.pdf`) with a live
      `OPENROUTER_API_KEY` — passed first try, no code changes needed. `cloudflare-ai`
      engine used (no OCR fallback triggered); document reached `READY`; heading recovery
      confirmed yes (39 sections, genuine 3-level tree, 110 chunks); page-level provenance
      confirmed does NOT survive (`page_start`/`page_end` stay document-wide despite
      per-page heading text) — both findings empirically confirmed, matching the documented
      expectations. Offsets manually spot-checked on two chunk groups, no corruption found.
      See memory.md "F23 empirical validation run" for full detail.
- [x] F24 Ingestion auto-dispatch (arq) — **built out of numeric order, this session
      (after F41), discovered as a gap during F51 frontend recon**: nothing had ever
      auto-advanced a document past `UPLOADED` — F20-F22's stage endpoints were always
      manual-trigger only, and no arq task had ever been registered (`WorkerSettings.
      functions` was `[]` since F00). New `platform/queue.py` (`JobQueue` Protocol +
      `ArqJobQueue`, same DI-not-a-seam treatment as `ObjectStore`) + new
      `ingestion/tasks.py` (3 arq job functions, registered in `worker.py`). On a genuine
      new upload (never a checksum-dedupe hit), `documents/router.py` calls
      `ingestion_service.enqueue_pipeline` — composed at the ROUTER, not
      `documents.service`, specifically to avoid a circular import (`ingestion.service`
      already imports `documents.service`). Each stage's job enqueues the next stage's job
      on success only; correctness under arq's at-least-once redelivery is guaranteed by a
      deterministic `job_id` (`f"ingestion:{stage}:{document_id}"`) that arq itself dedupes
      on — a before/after DB-status check alone was tried first and an independent review
      caught that it cannot prevent a concurrent-redelivery double-enqueue (the before-read
      happens in a separate transaction from the actual stage claim); fixed before this was
      recorded as done. **Known, accepted, named gap**: a lost enqueue — at upload time OR
      between stages — silently strands a document at whatever status it last reached; no
      sweeper/re-dispatch exists in F24. DoD met: `tests/test_ingestion_dispatch.py` (upload
      enqueues, dedupe doesn't re-enqueue, full chain advances via enqueue, sequential
      redelivery no-ops, job_id dedup is the actual concurrency backstop, org-scoping
      independent-backstop test). 105/105 suite green, ruff clean. Independent review:
      one blocking finding (the redelivery race above) and one minor (HTTP-path pool
      leaked per request) — both fixed.

## Phase 3 — Knowledge + Retrieval
- [x] F30 Notebooks (reference join) (`a85138e`) — new `app/knowledge` module (`Notebook`/
      `NotebookDocument` models mapped to the locked `knowledge_bases`/
      `knowledge_base_documents` table names; public API/schemas/routes/tests use "Notebook"
      terminology throughout). Migration `0008_notebooks.py`. CRUD for notebooks
      (create/update-metadata/delete/list) + idempotent attach/detach + list-documents-in-
      notebook, all org-scoped (`knowledge_base_documents` carries `org_id` directly, no
      scope-via-parent). Cross-module document existence/ownership validation goes through
      two new narrow `DocumentsService` methods (`get_document`, `list_by_ids`), each with a
      real caller in `knowledge.service` — not a repository/ORM import (a first draft that
      did this was caught and fixed in self-review, see memory.md). DoD met: a document
      attached to two notebooks shares one row (asserted directly in
      `tests/test_knowledge.py`); 14 new tests (CRUD, idempotency, tenant isolation incl.
      cross-org attach denial). 74/74 suite green, ruff clean.
- [x] F31 Flat retrieval (`c194b2b`) — new `app/retrieval` module (router/service/schemas
      only — no own table). `RetrievalService.search` scopes to `notebook ∩ allowed` via
      `knowledge.service.list_notebook_documents` + `resolve_allowed_documents` (MVP stub:
      all org docs via `documents.service.list_documents`, the V2 permissions hook), embeds
      the query via the `Embedder` seam, and calls the new
      `IngestionService.search_chunks` (kNN SQL stays in `ingestion/repository.py` since
      embeddings/chunks are ingestion's tables — `EmbeddingRepository.search_chunks` filters
      `org_id`/`owner_type='chunk'`/`model=:active_model`, `k` passed straight to `LIMIT`,
      no over-fetch). New `app/ingestion/schemas.py` (`ChunkHit`) carries the result shape
      across the module boundary. `assemble_context` (pure function) numbers hits into
      `ContextBlock`s — the shape F40/F41 will consume. `POST /retrieval/search`. DoD met:
      `tests/test_retrieval.py` — notebook-scoped search, empty-notebook no-op, k bounds
      (422 outside 1–50), API-level cross-org 404, a repository-level isolation backstop
      test calling `search_chunks` directly with a cross-org document id (independent of
      upstream scoping), active-model filtering (no duplicate hits after a re-embed),
      `resolve_allowed_documents` unit test, `assemble_context` unit tests. 83/83 suite
      green, ruff clean. Independent code-review pass: zero violations against the 7 hard
      rules.

## Maintenance — Package-layout refactor (no new feature work)
- [x] **Structural refactor, F00–F31 → package-per-module convention** (4 commits:
      `65845e1` platform/seams, `8a72e41` ingestion/service, `fadbb07` documents/repository,
      `f50bee4` documents/service) — promoted the 4 layer files that crossed the
      >200-lines-AND-2+-independent-responsibilities trigger from flat files to subpackages:
      `platform/seams.py` → `platform/seams/` (types/protocols/fakes/real_parser/real_llm/
      factory), `ingestion/service.py` → `ingestion/service/` (parsing/structuring/
      embedding/search), `documents/repository.py` → `documents/repository/` (folders/tags/
      documents), `documents/service.py` → `documents/service/` (folders/tags/documents, via
      free-function delegation — same convention as ingestion, no mixins). ZERO logic/
      behavior/schema/API change. All public + test-facing import paths preserved via
      `__init__.py` re-exports (including the two private `RealParser` helpers
      `tests/test_seams.py` imports directly). One pre-existing private import
      (`scripts/inspect_document.py` → `_build_sections_and_chunks`) was updated to its new
      path rather than re-exported, per direct instruction (don't promote a deliberately-
      private helper to a package's public API for one debug script). Deliberately did NOT
      touch `documents/models.py`/`ingestion/models.py` (multiple ORM classes but zero
      logic — not a violation), `documents/router.py`, `ingestion/repository.py`,
      `identity/*`, `knowledge/*`, `retrieval/*` (all under threshold or already correct —
      `retrieval/` has no `models.py`/`repository.py` since it owns no table, by design).
      Verified: each of the 4 commits individually green (83/83 suite, ruff clean) before
      committing; after all 4, ran the full migration chain against a **fresh** Postgres
      container (not just unit-test green) and confirmed all 11 expected tables + the 5
      module model imports in `migrations/env.py` still register correctly — unaffected
      since no `models.py` moved. **Convention now locked** in `architecture.md`
      ("Package-layout convention") + `orchestrator.md` (hard rule #8 + Implement/Review
      steps) + the `review` skill — applies to F40 onward. Reference module: `knowledge/`
      and `retrieval/` (small modules, no padding, already correct).

## Phase 4 — Chat
- [x] F40 Grounded generation (`1572fa8`) — new `app/chat` module (`schemas.py`/
      `service.py`/`router.py`/`exceptions.py` only — stateless, no `models.py`/
      `repository.py`/`tasks.py`/migration; F41/F42 own persistence). `POST /chat/ask`:
      `ChatService.ask` calls `retrieval_service.search` (hard rule #1 — the only
      cross-module call; never reimplements scoping/embedding/kNN), builds a strict
      grounding prompt (`build_messages`, pure function — numbered `[n]` context blocks +
      a fixed refusal sentence instruction), calls the `LLM` seam via
      `call_llm_with_retry`, returns `ChatResponse` carrying F31's `ContextBlock`s as
      citations unchanged (citation *resolution* is F41, not built here). Non-streaming
      this session (see buildplan.md's F40/F4x amendment + memory.md for the
      streaming-deferral rationale) — `generate_answer` is an async-generator core so the
      F4x SSE switch is router-only. Retry classification, the `LLM.model` seam addition,
      and the additive `ContextBlock.distance` touch to F31 are all recorded in memory.md.
      95/95 suite green (1 pre-existing skip), ruff clean. Independent code-review pass
      against hard rules #1/#3/#4/#8 + the F40 DoD: zero violations.
      **DoD's automated half met** (refuses on empty context — `tests/test_chat.py`,
      enforced structurally by `_SYSTEM_PROMPT` and exercised against a now
      context-aware `FakeLLM`). **DoD's manual acceptance gate SATISFIED (2026-06-24,
      this session)** — ran the real pipeline (real parser + real embedder + real LLM,
      all via the OpenRouter key, `pdf/kech104.pdf`) against an in-scope and an
      out-of-scope question, end to end through `chat_service.ask`. In-scope ("Kossel-Lewis
      approach... octet rule") retrieved tight, relevant distances (0.35–0.42) and got a
      correctly grounded answer citing `[1][6]`. Out-of-scope ("2022 FIFA World Cup
      winner") retrieved loose, irrelevant distances (0.80–0.90) and the real LLM returned
      the exact fixed refusal string verbatim, with no fallback to its own training
      knowledge. Config/script-only validation — zero production code changed (see
      memory.md "F40 manual acceptance gate" for full detail, including the
      fake-embedder-can't-prove-the-positive-case finding and the one-OpenRouter-key-feeds-
      all-3-seams architectural note).
- [ ] F4x SSE streaming for chat (deferred out of F40 — see buildplan.md)
- [x] F41 Citations (offset mapping + persisted) — new `app/chat/models.py`/`repository.py`
      (`Conversation`/`Message`, migration `0009_conversations_messages.py`). Citations are
      now derived from the model's `[n]` markers actually present in the answer
      (`parse_citation_markers`, pure regex function) instead of every block retrieval
      returned (F40's prior shape). Each resolved citation is rebuilt from a FRESH
      `ingestion_service.get_chunks(ctx, chunk_ids)` read of the source-of-truth `chunks`
      row (new narrow accessor, backed by new `ChunkRepository.get_by_ids`, org-scoped
      independently — same precedent as F31's `search_chunks`/F30's `get_document`), not
      from the `ContextBlock` copy retrieval already had in hand — this is the provenance
      round-trip the DoD's "clicking a citation shows the exact source span" requires, and
      it's directly tested (`test_ask_citation_provenance_round_trip_matches_stored_chunk`).
      An out-of-range/malformed/missing-chunk marker is dropped silently (logged, never
      raised, never fabricated — `test_ask_out_of_range_marker_is_dropped_not_fabricated`).
      Every `/chat/ask` call creates a brand-new `Conversation` + user `Message` + assistant
      `Message` (citations jsonb) — no conversation reuse/multi-turn threading yet
      (deliberate scope decision, see memory.md — reuse waits for a future history-
      threading feature). `chat/` stayed flat (no subpackage promotion; reviewed against
      hard rule #8 and judged still one cohesive pipeline). DoD met. 99/99 suite green (1
      pre-existing real-parser test deselected), ruff clean. Independent code-review pass:
      zero violations against hard rules #1/#3/#4/#8.
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
**Phase 2.5 COMPLETE: F23 Real parser integration** (code `9e7f319`, docs `90285c2`,
empirical validation run and confirmed this session — real 36-page PDF reached `READY` via
`cloudflare-ai`, heading recovery confirmed, page-provenance gap confirmed, offsets sane).
**Phase 3 COMPLETE: F30 Notebooks (`a85138e`), F31 Flat retrieval (`c194b2b`)** — new
`app/knowledge` module (migration `0008_notebooks.py`) and new `app/retrieval` module
(no migration — reads existing F21/F22 tables via `ingestion.service.search_chunks`).
83/83 suite green, ruff clean — see memory.md "F30 Notebooks" / "F31 Flat retrieval" for
full detail.
**Maintenance (prior session): structural package-layout refactor complete** (4 commits,
zero behavior change, convention now locked in architecture.md/orchestrator.md/review skill
— see "Maintenance" section above).
**Phase 4 IN PROGRESS: F40 Grounded generation (`1572fa8`)** — non-streaming `POST /chat/ask`,
retrieval-only cross-module call, retry-on-transient-only LLM seam call. **Manual
acceptance gate SATISFIED (2026-06-24)** — real parser+embedder+LLM run against
`pdf/kech104.pdf` confirmed grounded answer on an in-scope question and the exact refusal
string (no fabrication) on an out-of-scope one. F40 fully done.
**F41 Citations DONE (this session, feature commit pending)** — `chat/` gained persistence
(`app/chat/models.py`/`repository.py`, migration `0009_conversations_messages.py`:
`conversations`+`messages`). Citations now resolve from the model's `[n]` markers actually
in the answer (not every retrieved block), rebuilt from a fresh `ingestion_service.
get_chunks` read of the source-of-truth chunk row (new accessor, org-scoped
independently) — the provenance round-trip the DoD required, directly tested. Invalid
markers dropped silently, logged, never fabricated. Every `/chat/ask` creates a fresh
conversation+message pair (no reuse/multi-turn yet — deliberate, see memory.md). 99/99
suite green, ruff clean, independent review: zero hard-rule violations. F4x (SSE) and
F42 (admin debug bundle) not started.
**F24 Ingestion auto-dispatch (arq) DONE — built out of numeric order, THIS session,
chronologically AFTER F41**, discovered as a real gap during F51 frontend recon: nothing
had ever auto-advanced a document past `UPLOADED` (F20-F22 were always manual-trigger
only; `WorkerSettings.functions` was `[]` since F00). New `platform/queue.py`
(`JobQueue`/`ArqJobQueue`) + `ingestion/tasks.py` (3 chained arq jobs); upload triggers
the chain via `documents/router.py` (composed at the router, not `documents.service`, to
avoid a circular import); correctness under redelivery is guaranteed by a deterministic
arq `job_id`, not the before/after status check alone (an independent review caught and
this session fixed a real concurrent-redelivery double-enqueue race in the first draft).
**Known, accepted gap**: a lost enqueue (upload time or mid-chain) strands a document
silently — no sweeper/re-dispatch built. 105/105 suite green, ruff clean. See memory.md
"F24 Ingestion auto-dispatch" for full detail.
**A future cold-start reading dates: F24 lands after F41 in the commit timeline — this is
intentional, not a mistake.** It exists because F51 (frontend) recon surfaced that upload
alone never produced a queryable document.
Next action: **F51's upload UI now against an actually-advancing pipeline** (folders/tags/
upload, with a minimal Vitest+RTL harness introduced alongside it per this session's
decision) — or F4x SSE streaming / F42 admin debug bundle, ask the user which to resume.
**Resolved (2026-06-23):** `GET /context/docs` was deleted (decision: too risky to ship,
not org-scoped) — see buildplan.md "Unplanned additions".
