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
- [x] F05 LocalDiskObjectStore (offline-first storage) (`8c44c75`, code; docs this session) —
      **built out of order**, surfaced now to unblock the F51 manual gate (no R2 creds in this dev
      env → real upload 500s in boto3; a `dependency_overrides` shim can't help because the arq
      worker calls `get_object_store()` directly and never sees the override — its parse/structure
      storage calls would still build real R2 and 500 mid-walk). New `LocalDiskObjectStore` in
      `app/platform/storage.py`: a second implementation of the **existing** `ObjectStore` put/get
      port (no `exists`/`delete` — Option A, those ride with the future orphan-sweep feature),
      selected by new `STORAGE_MODE=r2|local` config (default `r2`, mirroring the seam `*_MODE`
      fake-default — production unchanged) via `STORAGE_LOCAL_ROOT`. `get_object_store()` is the
      single selection point, so the API process and the worker share one store and the same blobs.
      Keys map 1:1 to nested dirs (byte-identical round-trip across parse→structure→embed); `get`
      raises `FileNotFoundError` on a missing key — the real parity property, since the ingestion
      callers catch `except Exception` broadly so local `FileNotFoundError` and boto3 `ClientError`
      take the same not-found branch (verified at `parsing.py:67/78`, `structuring.py:217`).
      Deliberately not production-durable (no multipart/concurrency/fsync — noted in code so it
      isn't over-built). DoD met: `tests/test_storage.py` (round-trip, nested-key-dir creation,
      get-raises-on-missing, factory selects local, default stays r2, unknown-mode rejected) — fully
      offline, no creds, CI-offline invariant preserved. 135/135 suite green (1 real_parser
      deselected), new files ruff clean (the 3 standing `scripts/inspect_document.py` findings are
      pre-existing, untouched).

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
- [x] F25 Folder move/rename/delete — **built out of numeric order, this session**, well
      after F11/F12/F24/F30/F31/F40/F41, surfaced by a real client requirement (folders
      must be moveable/renameable/deletable; heavily navigated, deeply nested, moves rare
      and allowed to be slow). Preceded by a dedicated analysis+design session (no code)
      that confirmed the actual F11 model (materialized path, parent_id FK, no rename/move
      code at all, and an unconditional cascading delete with zero non-empty check — a live
      data-loss bug) and chose **parent-pointer (adjacency list)** over keeping materialized
      path as authoritative — `path` is now a non-authoritative display cache, rebuilt
      synchronously in the same transaction as any move/rename.
      New `FolderRepository.list_subtree` (BFS over `parent_id`, parent-before-child order)
      backs both the cycle check and the path rebuild; `_rebuild_subtree_paths` derives
      every path strictly from parent_id+name (never by slicing the old path string — the
      bug class the design session explicitly flagged, since slicing risks false-matching a
      sibling with a shared name prefix, e.g. "HR" vs. "HR-Archive"). `_relocate_folder` is
      one shared helper for both rename and move (same correctness shape: cycle check via
      subtree-membership, target-scoped name-collision check excluding the folder's own
      row, same-transaction rebuild); a `_UNCHANGED` sentinel distinguishes "don't touch
      parent_id" (rename) from "move to root" (`parent_id=None`, a real move). New
      `PATCH /documents/folders/{id}` (rename) and `POST /documents/folders/{id}/move`
      (move) — deliberately two endpoints, not one combined PATCH, since a single optional
      `parent_id` field can't distinguish "unchanged" from "move to root" without inventing
      a sentinel in the wire schema too.
      Delete gained 3 modes (`?mode=block|cascade|reflow`, default `block`): `block` (the
      fix for the prior unconditional-cascade bug) 409s on any direct child folder or
      document; `cascade` is an explicit, confirmed action relying on the existing FK
      behavior (`Folder.parent_id` `ON DELETE CASCADE` removes the subtree;
      `Document.folder_id` `ON DELETE SET NULL` means documents anywhere in the subtree
      survive, orphaned to org root — never deleted, consistent with architecture.md's
      "folder is NOT a permission boundary"); `reflow` moves only the deleted folder's
      DIRECT children (folders and documents) up to its parent, reusing the same
      subtree-rebuild helper per reflowed child folder, then deletes the now-empty folder.
      Zero migration needed — `parent_id` already existed; this was logic-only.
      **Residual race, accepted and tested**: the collision check and the write share one
      transaction (per direct instruction), but read-committed isolation can't fully
      serialize two truly concurrent moves — `uq_folders_org_parent_name`'s `IntegrityError`
      is caught and translated to `FolderNameConflict` (409), never a raw 500, same
      precedent as F12's checksum-dedupe race. **New finding surfaced while testing this
      backstop, not introduced by F25**: that constraint provides NO protection between two
      ROOT-level folders sharing a name (`parent_id IS NULL` on both — Postgres treats
      NULL≠NULL for uniqueness), so a genuine concurrent race at root level could still
      produce duplicate root names with no constraint to catch it. Only the
      application-level check protects root-level names today; flagged, not fixed (fixing
      it means an `ALTER ... ADD CONSTRAINT ... NULLS NOT DISTINCT` migration, out of this
      feature's approved scope). Independent code-review pass: zero hard-rule violations;
      one minor note (reflow-delete's per-child collision race is correctness-equivalent to
      move/rename's but has weaker direct test coverage of the constraint-backstop firing,
      vs. rename's dedicated monkeypatch-forced-race test) — accepted as-is, not blocking.
      `tests/test_folder_moves.py` (20 new tests): rename, multi-generation path rebuild
      with a sibling sharing a name prefix (the strong regression test), move-to-new-parent,
      move-to-root, all 3 cycle depths (self/direct child/deep descendant), both collision
      directions, the constraint-backstop-to-409 translation, block/cascade/reflow delete
      (cascade tested with documents at every depth of a 3-level subtree), reflow-at-root,
      reflow-collision, cross-org 404 for all 3 ops, and a repository-level cross-org
      target-parent backstop. 125/125 suite green, ruff clean.
- [x] F25 follow-up: folder create hardening + root-uniqueness backstop — closes the
      finding above, plus a more severe, genuinely pre-existing, separate bug surfaced
      while fixing it: `create_folder` had NO duplicate-name check or `IntegrityError`
      handling at all (any duplicate-name create, root or sibling, raised an unhandled
      500 — `_relocate_folder` had this discipline from F25, `create_folder` never did).
      New partial unique index `uq_folders_org_root_name ON folders (org_id, name) WHERE
      parent_id IS NULL` (migration `0010_folder_root_uniqueness.py`) closes the root-NULL
      gap; `create_folder` now mirrors `_relocate_folder`'s check-then-act +
      `IntegrityError → FolderNameConflict` shape. 3 new tests (2 plain duplicate-create,
      1 forced-race root backstop). 128/128 suite green, ruff clean, independent review
      clean. See memory.md for full detail.

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
- [x] F4x SSE streaming for chat (`e0d67df`) — new `POST /chat/stream` endpoint alongside
      the existing `POST /chat/ask` (JSON endpoint preserved, all 99+ tests untouched).
      New `ChatService.stream_ask` async generator in `service.py`: yields
      `{"type":"token","content":"..."}` events as the LLM produces output, then a final
      `{"type":"done",...}` event carrying the persisted `conversation_id`/`message_id`/
      `citations` (identical persistence logic to `ask`). No mid-stream retry — once tokens
      are flowing the client has partial output; errors after first token yield
      `{"type":"error"}` via a broad `except` in the router's `event_generator`. The
      F40-era prediction ("F4x SSE switch is router-only") was confirmed: `generate_answer`
      and `call_llm_with_retry` were untouched; only a new `stream_ask` method was added
      to `ChatService`, and the router adds a new endpoint to iterate and yield SSE lines.
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
- [x] F51 Repository UI (folders/tags/upload + status) — DONE, this session. New
      `documentsApi` namespace in `lib/api.ts` (typed to the real `app/documents` schemas),
      `features/documents/{FolderTree,DocumentList,DocumentsPage}.tsx`,
      `components/StatusBadge.tsx` (shared). Folder tree built client-side from the flat
      `parent_id` list; create/rename/move/delete all wired to the real F25 endpoints, every
      mutation does a WHOLE-list refetch (no optimistic patch — F25's move/rename rebuild
      every descendant's `path` server-side). Document list polls every 2s while any visible
      document is non-terminal, stops once all are READY/FAILED (`pollIntervalFor`, unit
      tested directly). Upload via a plain file input (not the originally-sketched
      drag/drop `UploadDropzone` — no backend progress signal to show, recorded as a scope
      reduction in uiregistry.md, not a gap). Fixed a real pre-existing bug in `apiFetch`
      found while wiring multipart upload: it would have forced `Content-Type:
      application/json` onto FormData bodies, breaking the multipart boundary — confirmed
      the separate, unconditional Authorization-header code path still applies to uploads.
      First-ever frontend test runner introduced (Vitest+RTL, explicit imports not
      `globals: true`, manual `afterEach(cleanup)` in `src/test/setup.ts`) — 14 tests, all
      mocking `documentsApi` only, never a real backend. New `frontend` CI job, independent
      of the backend job (no Docker needed). `tsc -b`/`vite build` clean. Manually verified
      end-to-end in a browser via Playwright against the real backend (create/rename/delete
      folder, real PDF upload, status display) — hit the same R2-credentials-missing dev
      gap F40 already documented; worked around with a throwaway local-disk storage
      override script, never committed. Independent code-review pass: zero issues on the
      3 explicitly-flagged points (proxy scoping, no-optimistic-updates, auth survives the
      FormData fix); 2 minor findings fixed (StatusBadge unsafe type cast; uiregistry.md
      updated via Imprint). See memory.md for full detail.
- [x] F52 Notebook + chat UI (`e0d67df`) — four new components: `NotebookList`
      (card list, inline create with Enter key, delete with confirm), `NotebookPage`
      (three-column layout: w-72 docs panel + `ChatPanel` (flex-1) + `CitationPanel`
      (w-80, conditional)), `ChatPanel` (streamed bubbles, typing indicator ●●●, inline
      `[n]` citation markers as buttons), `CitationPanel` (source span viewer with
      `border-l-2 border-accent` quote style). New `notebooksApi` and `chatApi` namespaces
      added to `lib/api.ts`. `chatApi.streamAsk` uses `fetch`+`ReadableStream`+`TextDecoder`
      (NOT `EventSource`, which only supports GET); returns cleanup `() => void` stored in
      `abortRef` for unmount/re-submit cancellation. `accumulated` local variable pattern
      captures tokens without stale closure. `citations?: ResolvedCitation[]` sentinel:
      `undefined` = streaming, defined (even `[]`) = final. Proxy entries for `/notebooks`
      and `/chat` added to `vite.config.ts`. 15 new Vitest+RTL tests (30/30 total); `tsc -b`
      + `vite build` clean. No separate manual gate run (requires real backend + Docker);
      component logic tested via mocked `chatApi.streamAsk`.

## Phase 6 — Security Hardening (after MVP validated, before real customer data)
- [ ] F60 Enforced RLS (RLS_ENABLED on; app_user/migrator split; FORCE RLS; teeth-having isolation test)

---
**Demoable milestone reached:** [x] end of Phase 4 (`e0d67df` — F4x SSE + F52 Notebook/Chat UI complete)

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
**F25 Folder move/rename/delete DONE — built out of numeric order, THIS session, after
F24/F30/F31/F40/F41**, surfaced by a real client requirement. Preceded by a dedicated
analysis+design session (no code) that confirmed F11's folder model had zero rename/move
code and an unconditional cascading delete (a live data-loss bug). Chose **parent pointer
over materialized path** (path demoted to a synchronously-rebuilt, non-authoritative
display cache) — decided primarily for the deferred V2 folder-permissions layer (stable
`folder_id`, no rewrite-on-move) and move atomicity, not performance. New
`FolderRepository.list_subtree` (BFS) + `_rebuild_subtree_paths` derive every path from
parent_id+name only, never by slicing the old path string (the bug class explicitly
avoided — false-prefix-matching a sibling like "HR" vs. "HR-Archive"). Delete gained 3
modes (`block` default — the fix for the prior unconditional-cascade bug —
`cascade`/`reflow`). A real, pre-existing gap was surfaced (not introduced) while testing
the concurrent-race backstop: `uq_folders_org_parent_name` provides no protection between
two ROOT-level folders sharing a name (Postgres NULL≠NULL) — flagged, not fixed, out of
this feature's scope. 125/125 suite green, ruff clean, independent review clean (one
minor, accepted note). See memory.md "F25 Folder move/rename/delete" for full detail.
**F51 Repository UI (folder tree + document list + upload) DONE — this session.** New
`documentsApi` (`lib/api.ts`), `features/documents/{FolderTree,DocumentList,
DocumentsPage}.tsx`, shared `components/StatusBadge.tsx`. Whole-folder-list refetch on
every mutation (no optimistic patch, since F25's move/rename rebuild every descendant's
path server-side); document list polls only while non-terminal. First-ever frontend test
runner introduced (Vitest+RTL) plus a new independent `frontend` CI job. Manually verified
end-to-end against the real backend via Playwright. See memory.md "F51 Repository UI" for
full detail, including a real `apiFetch` multipart-Content-Type bug found and fixed.
**F4x SSE streaming + F52 Notebook/Chat UI DONE — this session (`e0d67df`).** Backend:
new `POST /chat/stream` SSE endpoint + `ChatService.stream_ask` async generator, keeping
`POST /chat/ask` JSON endpoint intact. Frontend: `notebooksApi` + `chatApi` namespaces,
four new components (NotebookList/NotebookPage/ChatPanel/CitationPanel), Vitest+RTL tests
30/30 green, `tsc -b` + `vite build` clean.
Next action: **F42 admin debug bundle, or F60 RLS hardening** — ask the user which to resume.
**Resolved (2026-06-23):** `GET /context/docs` was deleted (decision: too risky to ship,
not org-scoped) — see buildplan.md "Unplanned additions".
