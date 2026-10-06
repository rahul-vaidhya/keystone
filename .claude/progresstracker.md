# progresstracker.md — Living State

Updated by the **Remember** skill after every successful Review. Tick a box only when its
Definition of Done (see `buildplan.md`) is met. Add the commit ref next to completed items.

---

> **Note:** The codebase underwent TWO major refactors: (1) 2026-07-01, domain-first
> (`app/<domain>/`) → layer-first MVC (`app/{models,schemas,controllers,services,repositories,exceptions,tasks}/`)
> with frontend `src/features/` → `src/{models,controllers,views}/`. (2) 2026-07-02,
> layer-first → single-MVC (Express-style backend with `routes/`+`controllers/` split,
> collapsed `services/`, frontend conventional SPA with `src/{types,services,pages,components,layouts}`).
> **Every `app/<domain>/...` and `src/features/...` path referenced in the entries below describes
> the layout at the time that work was done and is STALE.** The authoritative mapping (all three
> layouts) lives in `.claude/memory.md` ("Single-MVC re-refactor" section at the top).

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
      engine used (no OCR fallback triggered); document reached `READY` (39 sections,
      110 chunks); page-level provenance confirmed does NOT survive (`page_start`/
      `page_end` stay document-wide). Offsets manually spot-checked on two chunk groups,
      no corruption found. **Correction (2026-07-01, re-validated with real embedder+LLM
      this session): the "39 sections" are NOT a genuine chapter/subsection tree** —
      re-running end to end and inspecting the raw parsing artifact directly showed every
      section is literally `document.pdf > Metadata > Contents > Page N` (one leaf
      section per PDF page); real headings (e.g. "4.1 Kössel-Lewis Approach") are fused
      into the page's body text with zero markdown/whitespace separator, so
      `_parse_markdown_outline`'s regex has nothing to detect — `cloudflare-ai` simply
      never emits `#`-style markers for genuine document headings on this class of
      (two-column academic) PDF, only page boundaries. Confirmed this is a vendor/engine
      characteristic, not a parsing bug: 5 different documents (this PDF plus 4 synthetic
      reportlab PDFs with real bold/large-font headings) all produced the exact same
      `Page N` wrapper. Per the locked "never fabricate" contract this is the CORRECT
      behavior, not a defect — see memory.md "Real-embedder retrieval validation" for
      full detail and the retrieval-quality implications (retrieval stays correct
      regardless, since F31 keys off chunk-content embeddings, not section labels).
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

- [x] **MVC layout refactor (2026-07-01, `6ff4be7`)** — backend restructured from
      domain-first (`app/identity/`, `app/documents/`, `app/ingestion/`, `app/knowledge/`,
      `app/retrieval/`, `app/chat/`) to layer-first (`app/models/`, `app/schemas/`,
      `app/controllers/`, `app/services/`, `app/repositories/`, `app/exceptions/`,
      `app/tasks/`); frontend restructured `src/features/` → `src/views/` with types/api
      namespaces split out of `lib/api.ts` into `src/models/` and `src/controllers/`.
      ZERO logic/schema/API/behavior change — pure import-path + file-location refactor,
      full spec in `docs/mvc-refactor-prompt.md` (kept in-repo as the historical record).
      Old domain dirs and `src/features/` fully deleted. `documents/service`,
      `documents/repository`, `ingestion/service` kept their subpackage shape (just
      moved). DoD met: `ruff check`/`ruff format --check` clean (only 3 pre-existing
      `scripts/inspect_document.py` findings remain), all new-path import smoke checks
      pass, `Base.metadata` registers all 13 tables, `GET /health`→200, 43/43 non-DB
      pytest tests pass, 30/30 frontend vitest tests pass, `tsc -b`/`vite build` clean.
      **Docker-gated verification CLOSED (2026-07-01, follow-up)**: full suite re-run
      against real Testcontainers Postgres — 135 passed, 1 skipped (opt-in `real_parser`
      test needing a live API key, unrelated to Docker), 0 failures. Confirms the
      refactor is a true zero-logic-change. See memory.md "MVC layout refactor" for
      the full old→new path mapping table. **Committed `6ff4be7` (2026-07-01)** after
      an independent 7-Haiku-agent re-audit against `docs/mvc-refactor-prompt.md` (all
      layers, both module-boundary rules, stale-import sweep, frontend split, docs) came
      back clean; re-ran green at commit time (135 backend / 30 frontend, ruff clean).

- [x] **Single-MVC re-refactor (2026-07-02, uncommitted)** — re-refactored the 2026-07-01
      layer-first MVC into a unified single-MVC: Express-style backend (routes/+controllers/
      split, repositories/exceptions/schemas collapsed into services/models/) + conventional
      React SPA frontend (types, services, pages, components, layouts). ZERO logic/schema/
      API/behavior change — pure path + file-location refactor. Executed by 5 sequential
      agent slices (4 backend, 1 frontend) with fixups. Owner decisions: (a) literal
      `routes/`+`controllers/` split; (b) full service/model collapse; (c) frontend `src/types/`.
      **Verification:** Route table proven byte-identical to pre-refactor HEAD via OpenAPI
      diff. **Docker-gated full-suite re-run CLOSED:** 135 passed, 1 skipped (opt-in
      `real_parser` test needing a live API key, unrelated to this refactor), 0 failures —
      exact baseline match to 2026-07-01 MVC refactor. **3 test-file fixes + openai SDK
      installed (v2.44.0):** `app/config/__init__.py` shadowing the `settings` submodule
      (fixed imports in conftest/test_tenant_session), monkeypatch string literals updated
      (test_ingestion_dispatch), migrations/0002 import updated (app.platform.config →
      app.config.settings — deliberate exception, schema untouched). **Live end-to-end
      smoke PASSED:** GET /health 200, signup/login/me/docs all working. `ruff check` clean
      (3 pre-existing scripts findings only). Frontend: `tsc -b` clean, 30/30 vitest, `vite build`
      clean. See memory.md "Single-MVC re-refactor" for full old→new mapping tables, gotchas,
      and verification record. Work staged via git mv, uncommitted on main.

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
- [x] F42 Admin debug bundle (`8dfe315`) — new `message_traces` table (migration `0014`,
      schema exactly as designed in architecture.md), persisting per-answer hits+distances,
      the exact final prompt sent to the LLM, and the raw model output — written in the same
      transaction as the conversation+message pair (`ChatService._persist`), for both
      `/chat/ask` and `/chat/stream` since they share `_persist`. New admin-gated
      `GET /chat/messages/{message_id}/trace` (`require_admin`), `MessageTraceNotFound` → 404.
      Frontend: `chatApi.getTrace`, an inline "Debug" toggle on assistant chat bubbles visible
      only to owner/admin, showing hits/final_prompt/raw_output, with a module-level cache so
      reopening the same message's trace doesn't re-fetch. DoD met: an admin can see why any
      answer was produced from the persisted trace, never recomputed.
      **Mid-review structural fix (caught and closed same session, not deferred)**:
      `services/chat.py` had grown to 435 lines with 3 repository classes + a new read-only
      responsibility — past the package-layout convention's promotion trigger. Split into
      `services/chat/repository.py` (the 3 repo classes, mirroring `services/ingestion/
      repository.py`'s precedent) + `service.py` (pipeline logic) + `__init__.py` (re-exports
      only the 4 names an external call site actually uses). Zero logic change, full suite
      re-ran green before and after. See memory.md "F42 Admin debug bundle" for full detail,
      including why this is a different split shape than `documents/` (by-subdomain) or
      `ingestion/` (by-pipeline-stage). 185/185 backend tests (180 prior + 5 new), 54/54
      frontend tests (52 prior + 2 new), ruff/tsc/build all clean. **This completes Phase 4.**

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

## Maintenance — Document hard-delete + folder-based access restriction
- [x] Document hard-delete (2026-07-12) — **built out of buildplan order**, a direct
      ask, not an F-numbered item. See buildplan.md "Unplanned additions" and memory.md
      for full detail. `DELETE /documents/{id}` (cascades via existing FKs to
      sections/chunks/embeddings/document_tags/knowledge_base_documents; new
      `ObjectStore.delete` removes the blob(s) after DB commit). Frontend:
      `DocumentList.tsx` delete button (confirm-guarded). DoD met: tests green,
      ruff/tsc/build clean, live HTTP + browser verification passed. **Still current —
      unaffected by the access-control rework below.**
- [x] ~~Folder-based access restriction (`folders.restricted` boolean)~~ — **SUPERSEDED
      same session**, fully removed (column dropped, endpoint deleted, FolderTree
      lock/unlock button deleted) and replaced by the Access Roles system below, per a
      direct follow-up ask. Do not resurrect this design; see memory.md for why.

## Maintenance — Access Roles (tag-based RBAC) + folder tree drag-and-drop
- [x] Access Roles + drag-and-drop (2026-07-12, same session) — **built out of
      buildplan order**, replacing the folder-restriction feature above at the user's
      direct request (custom roles instead of a binary flag; drag-and-drop instead of a
      `<select>` move dropdown). Full design: `docs/access-roles-dnd-plan.md`; full
      detail: memory.md. New tables (migration `0012`): `access_roles`,
      `user_access_roles`, `access_role_tags`, `folder_tags`. A tag becomes
      "access-controlling" only once granted to an Access Role — untagged/ungranted
      resources stay open to everyone, zero behavior change for the common case.
      `resolve_allowed_documents` rewritten around this (owner/admin bypass unchanged).
      Folder-tagging is admin/owner-only; document-tagging stays open to any member
      (unchanged) — accepted, named risk that a member could inadvertently gate a
      document by attaching an already-granted tag. New `PATCH /documents/{id}/folder`
      (`move_document`) — didn't exist before, required for drag-and-drop. Frontend:
      `FolderTree.tsx` — lock/unlock removed, tag badges + admin-only tag grant added,
      native HTML5 drag-and-drop for both folder-reparenting and dragging a document
      onto a folder (no new dependency, unlike a tree library such as
      `react-complex-tree` — deliberately rejected to keep this codebase's
      zero-UI-library style). New `pages/AccessRolesPage.tsx` (create role, grant/revoke
      tags, assign/remove members) plus a tag-creation control — a real UI gap (no
      tag-creation existed anywhere before) caught only during manual browser testing.
      DoD met: 158/158 backend tests, 44/44 frontend tests, ruff/tsc/build clean,
      migration applied to a fresh DB and the running dev DB. **Live browser
      verification, the real proof**: dragged folders to reparent (API-confirmed),
      created a tag + role via the new page, granted the tag, assigned an invited
      member, tagged a folder, uploaded+ingested a real document into it, and confirmed
      via `/retrieval/search` that an outsider member got zero results while the
      role-holding member got the grounded hit. One unrelated bug found+fixed during
      this verification: a stale `uvicorn` process running pre-migration code (needed a
      restart, not a code fix) — see memory.md "Gotcha" for the lesson.

## Maintenance — Auth hardening (member removal, session revocation, password change, login lockout)
- [x] Auth hardening (2026-07-13, committed `a13d307`) — **built out of buildplan order**, a direct
      ask to audit login/signup/org/member-management against current best practice and
      close the gaps. Full detail + design rationale: memory.md "Auth hardening" section.
      Migration `0013` adds `is_active`/`failed_login_attempts`/`locked_until`/
      `token_version` to `users`. Four pieces: (1) `PATCH /auth/users/{id}/status` — soft
      deactivate/reactivate a member (member removal), same guard shape as `change_role`;
      (2) instant revocation on every request via `current_user`'s existing per-request DB
      read (no new token-blacklist infra needed) plus a `token_version`/`"tv"` JWT claim
      checked on every access+refresh read; (3) `POST /auth/me/password` self-service
      password change (argon2-reverifies current password, bumps `token_version` to kill
      every other session, returns a fresh token pair so the changing session keeps
      working); (4) per-account (never per-IP) login lockout, 5 attempts / 15 min, explicit
      "temporarily locked" message. Deliberately deferred (needs a new email-provider seam,
      out of this round's scope per direct user choice): tokenized email-invite links,
      password-reset-via-email, MFA. DoD met: 173/173 backend tests (12 new), 52/52
      frontend tests (8 new), ruff/tsc/build clean, migration applied cleanly to a real
      Testcontainers Postgres container as part of the full suite run.

## Maintenance — Full-codebase Haiku-swarm review + /chat/stream tests
- [x] Codebase health review + streaming test suite (2026-07-13, committed `c7d9b50`) —
      **a direct ask, not an F-numbered item**: 7 parallel Haiku agents reviewed every
      slice (5 domain reviewers + 1 mechanical hard-rules grep sweep + 1 test runner).
      **Verdict: healthy** — all hard rules pass in every slice (SQL-in-repository,
      thin routes/controllers, org_id scoping, seam boundaries, ingestion idempotency,
      retry discipline, deterministic arq job_id, linear migration chain 0001→0013);
      baseline confirmed green by actually running everything. Only substantive
      finding: `POST /chat/stream` had zero backend tests — closed same session with
      7 new tests + `_parse_sse_events` helper in `tests/test_chat.py` (happy path,
      persistence parity, refusal, mid-stream LLM failure → error event, missing
      notebook, citation provenance round-trip, cross-org isolation), written and
      verified by agents, independently re-verified before commit. **New test baseline:
      180 backend passed, 1 skipped** (was 173). Minor findings recorded (not fixed)
      in memory.md "Full-codebase Haiku-swarm review" section.

## Phase 6 — Security Hardening (after MVP validated, before real customer data)
- [x] F60 Enforced RLS (2026-07-14, committed `4f09623`) — migration `0015` applies
      `ENABLE`+`FORCE ROW LEVEL SECURITY` + a `tenant_isolation` policy to all 18 tenant
      tables UNCONDITIONALLY (no flag gate — isolation must never depend on config;
      `RLS_ENABLED` is vestigial, default true, kept only because migration 0002 imports
      it), plus the `app_user`/`migrator` role split (migrator BYPASSRLS; app_user
      NOLOGIN — LOGIN provisioning per-environment) and grants. **The real work was
      un-drifting the plumbing**: the F02 `tenant_session` design had drifted — nothing
      called it; all ~62 session-opening call sites used bare `sessionmaker()`, so the
      GUC the policies key on was never set on any real path. All refactored to
      `tenant_session(ctx.org_id)`; new `auth_session(email)`/`set_org_guc` power the
      pre-tenant auth bootstrap (signup pre-generates the org id + sets the GUC before
      the INSERTs; login switches into the matched org mid-transaction for lockout
      writes; two SELECT-only `auth_email_lookup` policies scope the cross-org email
      reads to exactly the named email). New `MIGRATIONS_DATABASE_URL` (app runs as
      app_user in prod, Alembic as owner). A guard test bans bare `sessionmaker()`
      outside `config/db.py` so the drift can't return. **Real bug found by the teeth
      tests**: the policy predicate needs `NULLIF(current_setting(...), '')::uuid` — a
      committed transaction-local GUC resets to `''` (not NULL) on the pooled connection
      and `''::uuid` raises. DoD met: `tests/test_rls.py` connects as the restricted
      `app_user`, omits the app-level filter, and reads zero cross-tenant rows (plus
      unset-GUC → zero rows, WITH CHECK rejects cross-org writes, and a golden-path
      HTTP flow driven end-to-end as app_user). 191/191 backend tests (1 skip),
      ruff clean, fresh-DB chain 0001→0015 applied by the suite. See memory.md
      "F60 Enforced RLS" for full detail + ops notes (dev DB still at 0014).

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
**MVC layout refactor DONE + COMMITTED `6ff4be7` (2026-07-01).** Backend moved
domain-first → layer-first (`app/models/`,`schemas/`,`controllers/`,`services/`,
`repositories/`,`exceptions/`,`tasks/`); frontend moved `features/` → `views/` +
split `lib/api.ts` into `models/`+`controllers/`. Zero logic change. Old dirs deleted.
**Docker-gated re-run CLOSED (2026-07-01, follow-up):** full `pytest` against real
Testcontainers Postgres — 135 passed, 1 skipped (opt-in `real_parser`, needs a live
API key), 0 failures. `ruff check .` re-confirmed unchanged (3 pre-existing findings
only). MVC refactor now fully verified end to end. See memory.md "MVC layout refactor"
for the path-mapping table.
**Single-MVC re-refactor DONE and COMMITTED (`81bd90f`, confirmed via `git log` 2026-07-12
— an earlier "UNCOMMITTED" note here was stale).** Layer-first backend+frontend
re-refactored into Express-style backend (routes/controllers split, collapsed services)
+ conventional SPA frontend (types, services, pages, components). Route table byte-identical
to HEAD. **Docker-gated full-suite re-run CLOSED: 135 passed, 1 skipped, 0 failures.**
Fixed 3 test-file bugs (app/config shadowing, monkeypatch string literals, migrations/0002
import). Installed openai SDK (v2.44.0). Live end-to-end smoke (GET /health, signup/login/
auth/documents) passed. `ruff check` unchanged (3 pre-existing). Frontend: tsc/vitest/build
all clean. See memory.md "Single-MVC re-refactor" for full mapping tables, verification
record, and gotchas.
**Document hard-delete DONE and COMMITTED (`7012af2`, 2026-07-12).** `DELETE
/documents/{id}` (full purge, cascades via existing FKs + new `ObjectStore.delete`).
Frontend delete button in DocumentList. 141/141 backend + 35/35 frontend tests green at
commit time, live HTTP + browser verification passed. **The folder-based access
restriction half of that same commit (`folders.restricted`) is now SUPERSEDED** — see
below.

**Access Roles (tag-based RBAC) + drag-and-drop DONE (2026-07-12, same session,
UNCOMMITTED).** Replaces `folders.restricted` (column dropped, endpoint removed,
FolderTree lock/unlock button removed — migration `0012`) with Access Roles: custom
named roles, granted tags, assigned members; a tag only gates access once granted to a
role, so untagged/ungranted resources stay open (zero behavior change for the common
case). New `PATCH /documents/{id}/folder` for drag-and-drop. FolderTree rebuilt with
native HTML5 drag-and-drop (folder reparenting + drag-a-document-onto-a-folder) and tag
badges/grants; new `AccessRolesPage.tsx`. 158/158 backend + 44/44 frontend tests green,
ruff/tsc/build clean, migration applied to a fresh DB and the running dev DB. **Live
browser verification passed end-to-end**, including a real `/retrieval/search` proof
that role-based tag gating actually restricts an outsider member while allowing a
role-holding member. See memory.md "Access Roles (tag-based RBAC) + folder tree
drag-and-drop" for full detail, including the stale-uvicorn-process gotcha hit during
verification and the `docs/access-roles-dnd-plan.md` design record.

**Auth hardening DONE (2026-07-13, committed `a13d307`).** Member removal
(deactivate/reactivate via `PATCH /auth/users/{id}/status`), instant access revocation
on deactivation (enforced in `current_user`'s existing per-request DB read), self-service
`POST /auth/me/password` (invalidates every other session via a new `token_version` JWT
claim), and per-account login lockout (5 attempts/15 min, migration `0013`). 173/173
backend + 52/52 frontend tests green, ruff/tsc/build clean. Email-based invite links and
password-reset-via-email explicitly deferred (would need a new email-provider seam). See
memory.md "Auth hardening" for full detail, including a real transaction-ordering bug
(lockout counter write would've been silently rolled back) caught and fixed during
implementation.

**Correction (2026-07-13):** the Access Roles + drag-and-drop feature is actually
ALREADY COMMITTED (`cc3faf3`), confirmed via `git log` — a prior "still uncommitted"
note here was stale (same class of staleness this file has hit before; always trust
`git log` over a commit-status claim in these docs). Auth hardening was committed this
session.

**Full-codebase Haiku-swarm review + /chat/stream test suite DONE (2026-07-13, this
session, committed `c7d9b50`).** 7-agent review: all hard rules pass everywhere, no
critical/major bugs. The one substantive gap (zero backend tests for `POST
/chat/stream`) was closed the same session — 7 new tests in `tests/test_chat.py`,
suite baseline now **180 passed, 1 skipped**; ruff/tsc/vitest/build all clean. Minor
findings and a swarm-orchestration lesson recorded in memory.md.

**F42 Admin debug bundle DONE (2026-07-13, committed `8dfe315`; docs `f50200d`).** New
`message_traces` table (migration `0014`) persists hits/final_prompt/raw_output per
answer, written in the same transaction as the conversation+message pair, for both
`/chat/ask` and `/chat/stream`. New admin-gated `GET /chat/messages/{id}/trace`.
Frontend: admin-only inline "Debug" toggle on chat bubbles. A `/review` pass caught
`services/chat.py` had crossed the package-layout promotion trigger (435 lines, 3
repository classes) — split into `services/chat/{repository,service}.py` same session,
zero logic change. **185/185 backend + 54/54 frontend tests green, ruff/tsc/build
clean. This completes Phase 4 — only F60 (RLS) remains on the buildplan.** See
memory.md "F42 Admin debug bundle" for full detail.

**F60 Enforced RLS DONE (2026-07-14, committed `4f09623`).** Migration `0015`: FORCE RLS
+ policies on all 18 tenant tables, app_user/migrator split, auth_email bootstrap
policies; tenant_session un-drift across all 62 call sites + guard test; teeth-having
isolation test passes as a genuinely restricted role. New test baseline: **191 passed,
1 skipped.** Next migration: `0016`. **THE BUILDPLAN IS COMPLETE — all phases 0–6.**

**Full-system live validation + swarm re-review DONE (2026-07-14, no code changes).**
A direct ask: 6-Haiku-agent full re-review (verdict: healthy, zero critical/major —
minors recorded in memory.md) + live end-to-end accuracy testing with real OpenRouter
seams: `pdf/kech104.pdf` → READY (36 pages / 39 sections / 110 chunks / 110
embeddings), retrieval distances cleanly separate in-scope (0.27–0.56) from
out-of-scope (0.80+), chat 7/7 factually correct with correct citations + exact
refusals on out-of-scope and hallucination bait, SSE streaming verified, RBAC 13/13
(role-gated visibility, live untag effect, admin-gated trace, cross-org 404s), suite
re-confirmed 191 passed / 1 skipped. **One env defect found+fixed: `pypdf` missing
from the venv** (declared in pyproject; first real parse FAILED with "No module named
'pypdf'" — installed 6.14.2). Dev DB upgraded 0012 → 0015 (head); the "still at 0014"
ops note is closed. Embedding-enrichment decision: quality is good, NOT building
enrichment; revisit parsing granularity first if quality ever lags. See memory.md
"Full-system live validation" for the full record.

## Maintenance — Semantic outline + enrichment + hierarchical retrieval (V2 activated)
- [x] Semantic outline + enrichment + hierarchical retrieval (2026-07-15, committed `ce3eebd`).
      **A direct ask**: replace page-level parser structure with LLM semantic parser + build
      hierarchical (V2) retrieval. Orchestrated by Fable, all reads/writes by Haiku subagents
      (4 recon, 3 implementation slices, test-runner, independent reviewer, 3 fix agents, live
      validators). Three flag-gated features, ZERO migrations (schema pre-designed: `sections.
      summary`/`topics` existed since migration 0006; `embeddings.owner_type` generalizes):
      (1) **SEMANTIC_OUTLINE_ENABLED** (default false) + **SEMANTIC_OUTLINE_WINDOW_CHARS=24000**
      — new `app/services/ingestion/semantic_outline.py`, LLM proposes headings verbatim,
      offsets located by `text.find()` (never fabricated; unfound dropped), cached as
      `artifacts/semantic_outline.json` (reused on re-run), every failure falls back to parser
      outline (stage never fails). (2) **ENRICHMENT_ENABLED** (default false) +
      **ENRICHMENT_SECTION_CHAR_LIMIT=6000** — new `app/services/ingestion/enrichment.py` +
      `POST /ingestion/documents/{id}/enrich`, per-section LLM JSON `{summary, topics}`
      → `sections.summary/topics` + `owner_type='section'` embeddings (idempotent upsert),
      NEVER mutates document status (doc already READY/queryable; enrichment additive). (3)
      **HIERARCHICAL_RETRIEVAL_ENABLED** (default false) + **HIERARCHICAL_TOP_SECTIONS=8** —
      coarse kNN over section embeddings (new `SectionHit`, `search_sections`) then fine chunk
      kNN filtered by `section_ids`, falls back to flat on zero section/chunk hits (logs INFO),
      `ContextBlock`/response unchanged, flag-off path byte-identical to flat MVP. **THE bug:
      enrichment.py passed plain dicts to `llm.stream()`; `RealLLM` does attribute access
      → every section failed; root cause: test fake LLM ignored messages argument.** Fixed
      to `Message(...)` dataclasses + all fakes hardened to access `m.role`/`m.content` so
      dict-passing can never pass tests. **LESSON**: any new seam call site needs live
      real-seam check or fake exercising seam's argument contract. **Live validation** (real
      OpenRouter on `pdf/kech104.pdf` — historical page-level worst case): semantic outline
      recovered 31–32 of ~34 sections as REAL headings vs old `Page N` wrapper; enrichment
      35/35 sections summarized + 35 `owner_type='section'` embeddings (~120s); hierarchical
      retrieval confirmed serving in-scope query (`retrieval.hierarchical_used` DEBUG,
      fallbacks INFO); chat grounded + citations on-scope, exact refusal on bait. DoD met:
      full suite **218 passed, 1 skipped** (run twice for stability), `ruff check`/`ruff
      format` clean, independent review 13/13 hard-rule checks PASS. New test files:
      `test_semantic_outline.py` (10), `test_semantic_structuring.py` (4, prefix `semstr-`),
      `test_enrichment.py` (8, prefix `enrich-`), `test_retrieval_hierarchical.py` (5, prefix
      `hier-`). Committed `ce3eebd`. Next migration: `0016`.

## Maintenance — V2 hardening + real-seam hierarchical-retrieval eval harness
- [x] V2 hardening + eval harness (2026-07-16, this session — COMMITTED). **A direct ask,
      continuing straight from the 2026-07-15 V2 activation**: harden the rough edges that
      session's live validation surfaced, then build a real-seam eval harness to actually
      measure whether hierarchical retrieval improves grounding. Session ran across a
      session-limit interruption; resumed mid-task from an invoker-supplied state snapshot.
      **Part 1 (4 items, zero schema change, flag-off behavior byte-identical throughout)**:
      (1) `retrieval.hierarchical_used` promoted DEBUG→INFO (was invisible at default
      `LOG_LEVEL=INFO`, unlike its two fallback siblings which already logged at INFO); (2)
      `sections.topics` (populated since 2026-07-15, never read anywhere) surfaced via a new
      `SectionHit.topics` field + a new `retrieval.section_topics` DEBUG log — deliberately
      NOT wired into `ContextBlock`/chat response shape, which stays frozen by design; (3)
      semantic outline hardening (`app/services/ingestion/semantic_outline.py`): a shared
      `_is_generic_heading()` helper now filters LLM-proposed junk ("Contents"/"Page N")
      BEFORE char_end computation runs, and windows now overlap by
      `_WINDOW_OVERLAP_CHARS=500` (with adjacent-window-only dedup) so a heading straddling a
      window boundary is no longer silently lost — 4 new offline unit tests; (4) new
      admin-gated `POST /ingestion/enrich-backfill` (mirrors F42's `require_admin` pattern) →
      `IngestionService.run_enrichment_backfill` lists READY documents via
      `documents_service.list_documents` (service call, never a repository import) and
      re-runs the existing idempotent `run_enrichment_stage` per document — closes the gap
      that pre-flag documents had no way to get enriched. **A real gotcha found by the
      invoker mid-session**: `structlog.testing.capture_logs()` doesn't lift the app's
      `LOG_LEVEL=INFO` wrapper_class filter, so a DEBUG-level assertion inside it silently
      sees nothing — fixed with a temporary `wrapper_class` swap in the test, documented as a
      standing gotcha for any future DEBUG-level log assertion. Verification: offline suite
      grew to **229 passed, 0 skipped, 2 deselected** (11 net-new tests), stable across
      repeated synchronous runs, ruff clean, independent Haiku review PASS on all 8 hard
      rules (`app/services/ingestion/__init__.py` stayed at 175 lines, under the
      package-layout promotion trigger).
      **Part 2 (the primary ask)**: `backend/tests/test_hierarchical_eval.py` — opt-in
      (`hierarchical_eval` marker, registered + CI-excluded exactly like `real_parser`;
      verified plain `pytest -q`/CI can never trigger it and its own skip condition fires
      correctly). Real OpenRouter parser/embedder/LLM ingest `pdf/kech104.pdf` with all 3 V2
      flags on, ONCE, then reuse that corpus for 8 golden questions × 3 retrieval-mode
      comparisons (flat / hierarchical top_sections=8 / top_sections=4, mechanically graded
      via section-heading substring match + `capture_logs()` event-name assertions — never a
      subjective judgment call) + one real `/chat/ask` per question for evidence+provenance
      grading, plus 2 bait questions × 2 modes. **One design subtlety**: `_retrieve_hits`
      computes coarse-pass `s = max(k, HIERARCHICAL_TOP_SECTIONS)`, so the eval uses `k=4` for
      retrieval-only comparisons (else `top_sections=4` would be silently clamped back to 8
      by the default `k=8` and the sensitivity check would be a no-op). **Live result: winner
      tally flat=0, hierarchical=0, tie=8** — hierarchical matched flat's top-hit section
      selection on every single question, never better, never worse. Evidence PASS 8/8.
      Provenance PASS 6/8 at face value, but both "FAILs" were a harness measurement artifact
      (hint-matching missed an abbreviated heading and a parent-context heading, not a real
      mis-citation — manual inspection confirms 8/8 true provenance). `top_sections=4` vs `=8`
      agreement: 8/8, no sensitivity detected at this corpus size. Bait: 2/2 exact refusal + 0
      citations in BOTH modes. Semantic outline recovered 27 real chapter/subsection headings
      with zero junk surviving into a winning top-section — first empirical confirmation the
      Part 1 junk filter works on the real corpus. **Honest verdict**: on this
      single-document, ~36-page corpus, hierarchical retrieval is correct but doesn't
      measurably improve grounding over flat — flat was already precise enough that a coarse
      pre-filter has nothing to correct, extending the 2026-07-14 finding
      ("AI chunk-enrichment NOT needed now") rather than contradicting it. Recommend keeping
      `HIERARCHICAL_RETRIEVAL_ENABLED` off by default until a genuinely large multi-document
      notebook creates real pressure on flat's precision. Eval itself: **1 passed in ~219s**,
      not part of any CI/offline tally. See memory.md "V2 hardening + real-seam
      hierarchical-retrieval eval harness" for the full report table and detail. No new
      migration — next is still `0016`.

**Semantic outline + enrichment + hierarchical retrieval (V2 ACTIVATED, 2026-07-15, committed `ce3eebd`).** Three flag-gated features built with zero migrations (schema pre-designed): (1) **SEMANTIC_OUTLINE_ENABLED** replaces page-level parser structure with LLM semantic headings (cached artifacts/semantic_outline.json, every failure falls back to parser outline, stage never fails); (2) **ENRICHMENT_ENABLED** runs per-section LLM JSON extraction (summary/topics → sections.summary/topics + owner_type='section' embeddings), never mutates document status (doc already READY); (3) **HIERARCHICAL_RETRIEVAL_ENABLED** coarse kNN over section embeddings then fine chunk kNN, falls back to flat on zero section/chunk hits. **THE bug**: enrichment.py passed dicts to `llm.stream()`; RealLLM attribute-access failed; fixed with Message(...) dataclasses + hardened test fakes. **Live validation** on `pdf/kech104.pdf` (historical page-level worst case): semantic outline recovered 31–32 of ~34 real headings; enrichment 33/33 sections; hierarchical retrieval confirmed serving in-scope query. Chat: grounded answer + citations on-scope, exact refusal on bait. **New suite baseline: 218 passed, 1 skipped.** Independent review 13/13 hard-rule checks PASS. Committed `ce3eebd`.

**V2 hardening + real-seam eval harness (2026-07-16, this session).** Part 1: promoted `retrieval.hierarchical_used` to INFO; surfaced dormant `sections.topics` via a DEBUG log only (chat response shape untouched); hardened `semantic_outline.py` (junk-heading filtering + overlapping windows with adjacent-window dedup, fixing both gaps the 2026-07-15 validation named); added admin-gated `POST /ingestion/enrich-backfill` for pre-existing READY documents. New suite baseline: **229 passed, 0 skipped, 2 deselected**, ruff clean, independent review PASS on all 8 hard rules. Part 2 (the primary ask): `test_hierarchical_eval.py`, an opt-in real-seam eval harness — ingested `pdf/kech104.pdf` once with all 3 V2 flags on, ran 8 golden questions × 3 retrieval modes + real `/chat/ask` grading + 2 bait questions. **Result: hierarchical tied flat on every question (0 wins either way)** — correct but no measured grounding improvement on this single-document corpus, extending (not contradicting) the 2026-07-14 "AI enrichment not needed now" finding. Recommend keeping the flag off by default until a large multi-document notebook creates real pressure on flat's precision. A genuine new gotcha found: `structlog.testing.capture_logs()` doesn't lift the app's INFO log-level floor, so DEBUG-event assertions need a temporary wrapper_class swap. See memory.md for the full report table and verdict.

## Maintenance — UX audit: 4 critical findings fixed (2026-07-16, this session — COMMITTED `8964ec6`)
- [x] Fixed and live-verified all 4 CRITICAL findings from the published "Veratas —
      Product UX Audit" artifact: (1) chat history vanishing on navigation (new
      `GET /chat/notebooks/{id}/messages`, `ChatPanel.tsx` hydrates on mount); (2)
      `[object Object]` rendered for Pydantic validation errors (`http.ts`'s
      `extractErrorDetail` now joins `.msg` fields); (3) drag-only document move (new
      always-visible move-to-folder `<select>` in `DocumentList.tsx`, reuses the
      existing `PATCH /documents/{id}/folder`); (4) invite-by-typing-a-password
      (new migration `0016` `invite_tokens` table — first new tenant table since F60,
      ships its own RLS — + `POST /auth/accept-invite`, self-serve one-time invite
      links). 15 new backend tests + several frontend tests, all green; ruff/tsc/build
      clean. See memory.md "UX audit — 4 critical findings fixed" for full detail
      including two gotchas (stale dev uvicorn on port 8010; dev Postgres needs its
      own manual `alembic upgrade head`, separate from the test suite's Testcontainers
      run). **Committed `8964ec6`** (one commit, per direct instruction). Next migration: `0017`.
      The remaining 11 non-critical findings (High/Medium/Low) from the same audit are
      untouched.

## Maintenance — UX audit: 4 High findings fixed (2026-07-19 — committed `8861a7c`, frontend-only)
- [x] Fixed and live-verified all 4 HIGH findings from the same published "Veratas —
      Product UX Audit" artifact, continuing directly from the 2026-07-16 critical-
      findings session: (1) empty-notebook Ask now disables the input and explains why
      instead of giving the same generic refusal an out-of-scope question would
      (`ChatPanel.tsx`); (2) zero responsive breakpoints — added a standard Tailwind
      off-canvas drawer sidebar (`Sidebar.tsx`/`AppShell.tsx`, `lg:` cutover) + stacked
      single-column layouts for `NotebookPage.tsx`/`DocumentsPage.tsx`/`ChatPanel.tsx`'s
      citation panel below `lg:`, desktop rendering confirmed byte-identical; (3) native
      `alert()`/`confirm()`/`prompt()` everywhere except Auth — new accessible
      WAI-ARIA-pattern `Modal`/`DialogContext`/`useDialog` system replacing every native
      call site across 6 components/pages, plus a bespoke `FolderDeleteDialog` (two
      labeled buttons, typed-name safety check on the destructive option) replacing
      `FolderTree`'s old cascade/reflow `window.prompt`; (4) dead "Search" nav item —
      new `SearchPage.tsx` wiring the existing notebook-scoped `/retrieval/search`
      endpoint (no new backend endpoint — honest scope, since the backend has no
      global cross-notebook search), reusing the existing `CitationPanel` component for
      click-through. 19 files modified + 12 new files, 100% frontend (zero backend
      touched). **101 passed (14 test files, was 30 baseline before this session),
      `tsc -b` clean, `vite build` clean** — independently re-verified after each of
      the 4 fixes, not just subagent-reported. Live-verified in a real browser for all
      4: empty-notebook disabled state, desktop-unchanged responsive layout, a real
      cascade-delete-with-typed-confirmation round trip, and a real (uncached)
      `/retrieval/search` API round-trip. **NOT YET COMMITTED** — see memory.md "UX
      audit — 4 High findings fixed" for full detail, two gotchas (the browser
      automation's `resize_window` tool doesn't reliably change the rendered viewport
      in this sandbox — mobile-width visual proof is still outstanding; the dev `arq`
      worker wasn't running this session, so a populated non-empty search-results
      round-trip couldn't be demonstrated live). 7 findings remain untouched (4 Medium,
      3 Low). **Correction (2026-07-20):** committed as `8861a7c` — a prior "NOT YET
      COMMITTED" note here was stale by the start of the next session.

## Maintenance — UX audit: final 7 findings fixed (2026-07-20, this session — committed `8193f41`)
- [x] Fixed and live-verified the remaining 4 Medium + 3 Low findings from the same
      published "Veratas — Product UX Audit" artifact, **closing the audit entirely**
      (all 15 original findings now fixed across 3 sessions: 4 Critical `8964ec6`,
      4 High `8861a7c`, these final 7 `8193f41`). Orchestrated as one chat dispatching
      subagents in 3 file-overlap-respecting waves: (1) document table showed only
      title+status — new `documents.uploaded_by` column (migration `0017`) + uploader-
      email/date/size/pages columns + a click-through `DocumentDetailModal.tsx`; (2)
      citations showed only raw char offsets — chunk→section LEFT JOIN surfaces
      `page_start`/`page_end` on `ResolvedCitation`, shown as "Page N" alongside (not
      replacing) the existing char-offset line; (3) hover-only row actions had no
      keyboard equivalent (WCAG 2.1.1) — `group-focus-within:opacity-100 focus-visible:
      opacity-100` added to all 7 sites across `DocumentList.tsx`/`FolderTree.tsx`/
      `NotebookList.tsx`/`NotebookPage.tsx`; (4) empty notebooks had no onboarding
      guidance — 3 static starter-question chips in `ChatPanel.tsx`'s empty state,
      click-to-submit (deliberately static, not LLM-generated, to avoid a hidden
      per-view LLM cost); (5) display names were guessed from email — real
      `users.name` column (migration `0018`) + optional signup field + an improved
      email-derived fallback heuristic; (6) no copy/feedback controls on answers —
      working copy-to-clipboard + a UI-only (unpersisted, by design) thumbs up/down
      toggle; (7) status pill never animated — a small `animate-pulse` dot on
      non-terminal statuses only. **252 backend passed (2 skipped, was 229), 138
      frontend passed across 18 files (was 101)**, `tsc -b`/`vite build`/`ruff` all
      clean, both new migrations applied to the real dev Postgres (`0018` head).
      Live-verified end-to-end in a real browser: signup with a name → "Welcome, Priya
      Verify"; a real upload showing uploader/date/size in the table + a working
      detail modal; a notebook's starter chips producing a real cited answer with
      "Page 1" shown in the citation panel; working copy/feedback controls. See
      memory.md "UX audit — final 7 findings" for full detail, including two subagent-
      failure gotchas (a `failed` task-notification doesn't always mean zero progress —
      check `git status` before re-dispatching) and this dev environment's real
      entrypoints (`backend/main.py`/`worker.py`, not `app/main.py`/`app/worker.py`).
      **Committed and pushed `8193f41` → `origin/main`.** Next migration: `0019`.

## Maintenance — Embeddable website chatbot widget (2026-07-23 — committed `7ee4a0e`)
- [x] Embed widget feature built exactly per the pre-approved `docs/embed-widget-plan.md`
      (/architect output from an earlier 2026-07-23 chat; all scoping decisions confirmed
      there). Orchestrated as one chat dispatching Sonnet subagents per slice (backend,
      frontend, independent §9-checklist reviewer), every diff reviewed and every
      verification independently re-run by the orchestrator. **Backend**: migration
      `0019` (`widgets` table with full 0016-pattern RLS block + plaintext globally-unique
      `public_id`; `conversations.widget_id` nullable FK), new embed domain
      (`models/services/controllers/routes/embed.py`, services file FLAT per convention),
      admin CRUD under `require_admin`, public no-auth `GET .../config` +
      `POST .../stream` (SSE) with anti-enumeration WidgetNotFound→404, origin allowlist
      (empty = allow-all with UI warning), NEW `app/utils/rate_limit.py` (Redis
      fixed-window, DI-selected like ObjectStore — offline suite needs no Redis),
      per-widget + per-IP limits, `stream_ask`/`_persist` optional `widget_id=None`
      threading (authenticated path byte-identical). Validation runs BEFORE the
      StreamingResponse is built so 404/403/429 are real HTTP statuses. **Frontend**:
      `public/widget.js` (ES5 IIFE bubble+iframe), public `/embed` `EmbedChatPage`
      (outside ProtectedRoute/AppShell, bare-fetch SSE, no auth), admin `/app/embed`
      `EmbedWidgetsPage` (create/list/revoke/delete via useDialog, copy-able script
      snippet + iframe URL), vite proxy scoped to `/embed/widgets`+`/embed/public`
      (bare `/embed` stays the SPA page). **Verified**: backend 269 passed / 2 skipped
      (+17 incl. the anonymous-ctx tag-gating pin), frontend 151 passed (+13; 2
      pre-existing files flake only under parallel vitest — pass with
      `--no-file-parallelism`), ruff FULLY clean (the 3 historical
      `scripts/inspect_document.py` findings no longer exist), `tsc -b`/`vite build`
      clean, migration applied to Testcontainers AND dev Postgres (`0019 (head)`).
      Independent review: PASS 12/12, zero blocking findings. **Live E2E in a real
      browser**: real org/upload/notebook, widget created in admin UI, snippet on a
      scratch `localhost:8888` page → bubble → iframe → real streamed cited answer;
      conversation persisted with `widget_id` set + `user_id` NULL; origin rejection
      403 proven via `127.0.0.1:8888` (different origin string); revocation → public
      404 "This chatbot is unavailable." See memory.md "Embeddable website chatbot
      widget" for full detail + gotchas. **Next migration: 0020.**
      **Correction (2026-07-24):** committed as `7ee4a0e` — a prior "UNCOMMITTED" note
      here was stale by the start of the next session.

## Maintenance — Embed widget hardening: reverse-proxy IP + rate-limit sliding window (2026-07-24 — committed `b649754`, pushed to `origin/main`)
- [x] A direct ask: review the embed-widget feature thoroughly (full code read,
      independent test re-run, live browser E2E, best-practice research — zero code
      changes), then fix the two most significant findings from that review. New
      `get_client_ip()` (`app/utils/http.py`) only trusts `X-Forwarded-For` when the
      direct peer is a configured `TRUSTED_PROXY_IPS` entry (empty by default —
      byte-identical to before for direct connections), closing the gap where the
      public embed endpoint's per-IP rate limit would silently collapse into one
      shared bucket for every visitor behind a real reverse proxy/load balancer.
      `RedisRateLimiter` (`app/utils/rate_limit.py`) changed from a plain fixed-window
      counter to a sliding-window counter, closing the well-known boundary-doubling
      burst flaw. 11 new offline tests (`tests/test_client_ip.py`,
      `tests/test_rate_limit.py`) — suite now **280 passed, 2 skipped**. Live-verified
      twice: via `curl` (reproduced the exact bug with the setting unset, confirmed
      the fix with it set), and in a real browser via claude-in-chrome (happy path
      unchanged, rate-limit 429 shows the correct friendly UI message, origin
      allowlist 403 unchanged, zero console errors). See memory.md "Embed widget
      hardening" for full detail, including a uvicorn-defaults gotcha
      (`proxy_headers=True`/`forwarded_allow_ips="127.0.0.1"` out of the box) and a
      test-writing gotcha (sliding-window decay never reaches exactly zero within the
      same window — a boundary-adjacent assertion needs comparing two probes, not
      asserting an exact full-burst pass). **Follow-up, same session, at direct
      request: the remaining 2 review findings fixed too.** `PUBLIC_APP_URL` +
      the two `WIDGET_*_RATE_LIMIT_PER_MINUTE` settings documented in
      `backend/.env.example` (were silently undocumented — production footgun).
      New `frontend/public/_headers` (Netlify/Cloudflare Pages convention, Vite
      copies `public/` verbatim into `dist/`) sets `X-Frame-Options: DENY` +
      `frame-ancestors 'none'` on every real authenticated route, leaving the public
      `/embed` page unmentioned (correctly stays framable). **Honest caveat**: this
      project has no committed hosting config anywhere, so the header only takes
      effect if deployed to Netlify/Cloudflare Pages specifically; any other host
      needs the same rules replicated in its own config. Could not live-verify the
      actual header in a browser (no real deployment target exists) — verified
      instead via a real `vite build` (`dist/_headers` present) + full test suites
      unaffected (backend 280/2, frontend 151/151, `tsc -b` clean).

## Maintenance — Notebook privacy (per-person sharing) + folder-mutation Access-Role gate (2026-07-27, this session — committed `e01c0f9` + `695e78b`, pushed to `origin/main`)
- [x] Two direct-ask bugs, not buildplan items: (1) any org member could see/open/
      chat in any notebook regardless of creator — fixed with a notebook-privacy
      model (creator-only by default, **no owner/admin bypass** — the one place in
      this app where the system role owner/admin does NOT see everything), new
      per-person `notebook_shares` table (migration `0020`, full F60-pattern RLS),
      view+chat-only share recipients, 403 (not 404) on denial; (2) a member without
      Access-Role visibility into a tag-restricted folder could still rename/move/
      delete it, create a subfolder under it, or move documents into/out of it — the
      existing `resolve_allowed_documents` tag-gating rule (promoted to a shared
      `resolve_folder_effective_tags` in `access_roles.py`) now also gates folder
      MUTATION via new `resolve_accessible_folder_ids` + `FolderOut.can_manage`;
      **org owner/admin DO bypass this one** (unlike notebook privacy), per direct
      instruction. New exceptions `NotebookAccessDenied`/`FolderAccessDenied` → 403.
      Backend: **293 passed, 2 deselected** (was 280 + 13 new:
      `test_notebook_sharing.py`, `test_folder_access_gate.py`), ruff clean.
      Frontend: **156 passed** (was 151 + 5 new), `tsc -b`/`vite build` clean. New
      `ShareNotebookDialog.tsx`, `NotebookPage.tsx`/`NotebookList.tsx` ownership
      gating, `FolderTree.tsx` `can_manage` gating (rename/delete/drag/drop/+New
      folder). Migration `0020` applied to Testcontainers AND the real dev Postgres.
      **Live-verified end-to-end with two real accounts + `read_network_requests`**
      confirming actual HTTP status codes (403→200 transitions for both the
      notebook-share grant and the Access-Role assignment, including a real
      unauthenticated stranger correctly 403'd on `GET /chat/notebooks/{id}/
      messages` too, not just the notebook page itself). See memory.md "Notebook
      privacy (per-person sharing) + folder-mutation Access-Role gate" for full
      detail. **Committed `e01c0f9` (code) + `695e78b` (docs), pushed to
      `origin/main`.**

## Maintenance — P0 roadmap (research-production-agent-features.md), 5 features, sequential subagent build (2026-07-28, IN PROGRESS)
- [x] **Feature 1: Reranker seam** — 4th seam in `app/services/seams/` (protocols/fakes/
      factory shared, new `real_reranker.py`), `RERANKER_ENABLED` (default off, gate),
      `RERANKER_MODE` (fake|real), widen-then-rerank in `RetrievalService._retrieve_hits`
      (`RERANK_CANDIDATE_K`/`RERANK_TOP_K`), new nullable `ChunkHit`/`ContextBlock.
      rerank_score`. `RealReranker` = httpx client to a self-hosted BGE-reranker-v2-m3
      via HF TEI (new `docker-compose.yml` `reranker` service). Built by a subagent from
      the full confirmed `/architect` plan; independently re-verified by the orchestrator
      (not just the subagent's self-report) — full `git diff` read, full suite rerun
      (307 passed/2 skipped, up from 293), ruff clean, gate-off byte-identical proven by
      a dedicated regression test. **Uncommitted** — see memory.md "Feature 1: Reranker
      seam" for full detail incl. the embed-widget threading deviation and a real
      circular-import bug found+fixed.
- [x] **Feature 2: Reranker-score confidence gate** — lives in `chat/service.py`
      (`_weak_evidence_gate_fires`), no separate enable flag (structurally inert
      whenever `RERANKER_ENABLED=False`). New `RERANK_MIN_SCORE` (permissive default).
      Fires only when the top block's `rerank_score` is below threshold; skips the LLM
      call, persists normally via the unchanged `_persist`, new
      `ChatResponse.weak_evidence` field. Independently re-verified: 312 passed/2
      skipped (up from 307), ruff clean, `.env` restored. **Uncommitted** — see
      memory.md "Feature 2" for full detail.
- [x] **Feature 3: Hybrid search (BM25 + vector, RRF)** — migration `0021`
      (`chunks.content_tsv` generated tsvector + GIN, native Postgres, no extension),
      `ChunkRepository.search_chunks_lexical`, `distance` nullable on
      `ChunkHit`/`ContextBlock`, pure `fuse_rrf`, `HYBRID_SEARCH_ENABLED`/
      `HYBRID_CANDIDATE_K`. Composes correctly with the reranker feature's candidate
      widening. Two real bugs found+fixed same session (a `Computed()` ORM mapping
      fix for generated-column inserts; a `FakeReranker` crash on `distance=None`
      found by the orchestrator during review, not the implementing subagent).
      Independently re-verified: 321 passed/2 skipped (up from 312), ruff clean,
      `.env` restored, a live proof test showing hybrid promotes a rare-term match
      pure vector search misses. `services/retrieval.py` now 325 lines — still judged
      one cohesive concern, not promoted; worth a final look once all 5 features are
      done. **Uncommitted** — see memory.md "Feature 3" for full detail.
- [x] **Feature 4: `message_feedback` table + frontend wiring** — migration `0022`
      (full RLS, new tenant table), `FeedbackRepository` upsert on
      `(message_id, user_id)`, `ChatService.submit_feedback` reuses the existing
      notebook-privacy check (`knowledge_service.get_notebook`), `MessageOut.
      my_feedback` closes the "resets to blank on reload" gap. First feature this
      round to touch the frontend — `ChatPanel.tsx`'s thumbs buttons are now real
      (fire-and-forget POST + history-seeded state). Independently re-verified:
      backend 328 passed/2 skipped (up from 321), ruff clean, `.env` restored;
      frontend 159 passed, `tsc -b`/`vite build` clean. **Uncommitted** — see
      memory.md "Feature 4" for full detail.
- [x] **Feature 5: Golden-eval suite** — migration `0023`, new `evals` domain
      (models/services/routes/controllers), `chat_service.get_curation_snapshot` as
      the sole cross-domain read point, admin-gated `POST`/`GET /evals/golden-
      questions`, "Add to golden set" button in the chat Debug panel, opt-in
      `pytest -m eval` Ragas harness (unverified against a real ragas install, by
      design — properly skip-guarded). Required wiring in `main.py`, `migrations/
      env.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `vite.config.ts`.
      Independently re-verified: backend 336 passed/3 skipped (up from 328), ruff
      clean, `.env` restored; frontend 162 passed, `tsc -b`/`vite build` clean.
      **Uncommitted** — see memory.md "Feature 5" for full detail, including a
      subagent-orchestration lesson (a subagent's own backgrounded shell command does
      NOT auto-notify it the way the orchestrator's backgrounded Agent calls do —
      caught mid-flight via `git status`/`.env` check, fixed, resumed).

## ALL 5 P0 FEATURES COMPLETE AND COMMITTED (2026-07-28) — 6 commits, not pushed

- [x] `services/retrieval.py` split into a subpackage (`permissions.py`/`fusion.py`/
      `service.py`/`__init__.py`) — commit `3a54c25`, zero logic change, verified via
      full suite pass before/after.
- [x] Live browser verification of the 4 user-observable P0 features (2026-07-29,
      no code changes) — confidence gate, hybrid search, message_feedback, golden-eval
      all confirmed working end-to-end via claude-in-chrome against the real running
      app (real Postgres/Redis, real parser/embedder/LLM, migrations 0021–0023
      applied to the dev DB). See memory.md for full detail per feature.

## Maintenance — P1 roadmap (broad-query router + map-reduce, contextual retrieval, Notebook Overview), 2026-07-29, IN PROGRESS
- [x] **Feature 1: Broad-query router + map-reduce** — new `services/retrieval/
      mapreduce.py` (4th retrieval strategy, generic, reusable by Notebook Overview)
      + `services/chat/broad_query.py` (classifier + glue). `BROAD_QUERY_ENABLED`
      (default off) gate in `ChatService.ask`/`stream_ask`, byte-identical when off
      (regression-tested). Additive `ResolvedCitation.citation_type` (chunk/section).
      Independently verified by a fresh subagent: 353 passed/3 skipped (up from 335),
      ruff clean, zero hard-rule violations. **Committed `35137e4`.** See memory.md "P1
      roadmap" for full detail.
- [x] **Feature 3: Notebook Overview** — new `notebook_overviews` table (migration
      `0024`, full RLS), on-demand generate/cache via `mapreduce.py` reuse,
      `stale` flag on document attach/detach, `NOTEBOOK_OVERVIEW_ENABLED` gates
      generation only (not GET). `services/knowledge.py` split into a subpackage
      (crossed the package-layout trigger as a result of this feature). Independently
      verified: backend 368 passed/3 skipped (up from 353), frontend 169 passed (up
      from 162), single migration head, ruff/tsc/build clean. **Committed `d593ece`.**
      One trivial cosmetic finding (harmless duplicate line in `models/knowledge.py`),
      not urgent. See memory.md "P1 roadmap" for full detail.
- [x] **Feature 2: Contextual retrieval** — zero-schema-change, hooked into
      `run_enrichment_stage` (re-embeds a section's chunks in place right after its
      summary is computed, reusing the existing `owner_type='chunk'` upsert).
      `CONTEXTUAL_EMBEDDING_ENABLED` (default off). Independently verified (2nd
      verifier attempt — the 1st stalled mid-run and left `.env` moved aside,
      recovered by the orchestrator before redispatching): 372 passed/3 skipped (up
      from 368), ruff clean, no regression to Features 1/3. **Committed `40b3b58`.**
      See memory.md "P1 roadmap" for full detail.

**ALL 3 P1 FEATURES COMPLETE AND COMMITTED (2026-07-29)** (`35137e4`/`d593ece`/
`40b3b58`, plus a docs commit `44dcb89`; a prior "none committed" note here was stale —
`git log` is authoritative, see memory.md's correction). Final baseline: backend 372
passed/3 skipped (started round at 335), frontend 169 passed (started at 162), single
migration head `0024`, ruff/tsc/build clean throughout. All 3 flags default `False` —
zero production behavior change until explicitly enabled. Pushed to `origin/main`.

## Maintenance — P1 hardening pass: citation wiring fix + 2 live-testing bug fixes (2026-07-29, committed `49e9a20` + `d17af09`, not pushed)
- [x] Direct ask: harden the P1 features via a subagent-per-item backlog (implementer +
      independent fresh verifier each time), main chat as orchestrator only. Item 1:
      frontend never wired up the backend's `citation_type`/`weak_evidence` fields —
      fixed (`ResolvedCitation` widened, `CitationPanel.tsx`/`ChatPanel.tsx` branch
      correctly, admin `TraceHit` also fixed for the section-trace shape found by the
      verifier). Frontend: 173 passed (up from 169), tsc/build clean. Item 2: first-ever
      live browser test of broad-query routing, Notebook Overview, and contextual
      retrieval — found Notebook Overview was ACTUALLY BROKEN live (returned the flat
      refusal string; a map-prompt wording bug in `mapreduce.py`), fixed and re-verified
      live; also fixed a smaller relevance-filter bug in the same file. Backend: 372
      passed/3 skipped (unchanged — behavior fix, no new tests). Independent verification
      caught one inaccurate implementer claim (a "pre-existing failures" claim that
      turned out false, though harmlessly — no real regression). Items 3 (reranker live
      smoke test — attempted, aborted on a Docker WSL2 memory/OOM issue unrelated to the
      code), 4 (real Ragas run), 5 (stale docs refresh) **deferred at user's request** —
      user will test those themselves. Session closed with the full dev stack
      deliberately shut down (all stray uvicorn/arq duplicates killed, Docker containers
      stopped) per direct instruction. See memory.md "P1 hardening pass" for full detail.

## Maintenance — Reranker live smoke test + 2 real bugs fixed (2026-07-30, committed `86ed560` + `447b9bb`, not pushed)
- [x] Closed item 3 from the 2026-07-29 backlog above: fixed the WSL2 memory ceiling
      (`.wslconfig` memory=10GB, permanent host-level change) that was OOM-killing the
      TEI reranker container; confirmed it now boots after ~11.5 min of genuine CPU
      warmup. Full live-testing pass with real seams verified all 6 P0/P1 features
      (reranker, hybrid search, confidence gate, broad-query, Notebook Overview,
      contextual retrieval) working end-to-end via claude-in-chrome + direct API/DB
      proofs. Found and fixed 2 real bugs surfaced by the live pass: (1) reranker
      transient failures (timeouts under the default `RERANK_CANDIDATE_K=25` candidate
      pool on CPU-only hardware) had no fallback and killed the whole chat turn — now
      degrades to unreranked hits, same fallback shape as hierarchical retrieval's
      flat-fallback; (2) `chat.stream_failed`/`embed.stream_failed` logged an empty
      `error=""` for message-less exceptions — now also logs `error_type`. 4 new
      regression tests. Backend: 376 passed/3 deselected (up from 372), ruff/format
      clean. Ragas eval harness (item 4) confirmed genuinely broken by an upstream
      ragas↔langchain_community incompatibility, not fixable from this project's side
      without further dependency work — not fixed this session. See memory.md for full detail.

## Maintenance — Stale context docs refresh (2026-07-30, committed `eb1607c`, not pushed)
- [x] Closed item 5 from the 2026-07-29 backlog: refreshed `architecture.md`/
      `codestandards.md`/`librarydocs.md`/`projectoverview.md` + `orchestrator.md`'s
      always-loaded summary against the real current shipped state (cross-checked
      against actual code, not just memory). Fixed stale RLS-deferred-to-Phase-6
      claims (3 of the 4 files — `architecture.md`'s own tenancy section was already
      correct), "3 seams"/reranker-as-unbuilt-V2 language throughout, a wrong
      `folders.path`-is-authoritative claim (F25 made it a display cache), a
      never-built "orphan sweep" described as if it existed, and added coverage for
      everything shipped since the original MVP sketch (hybrid search, confidence
      gate, broad-query router, Notebook Overview, contextual retrieval, notebook
      sharing, Access Roles, 7 new tables). Also found (not fixed, docs-only pass):
      `backend/app/{repositories,schemas,exceptions}/` are empty leftover dirs from
      the 2026-07-02 refactor, real cruft worth deleting sometime. See memory.md for
      full detail.

**All 5 items from the 2026-07-29 P1 hardening backlog are now closed** except the Ragas
dependency fix (needs real upstream work — pinning an older `langchain-community` or a
separate vertexai shim — before `pytest -m eval` can ever run).

## Maintenance — Repo cruft cleanup + real CI fix (2026-07-30, committed `b6ebacc`, pushed)
- [x] Deleted `backend/app/{repositories,schemas,exceptions}/` (the empty leftover dirs
      flagged during the docs refresh above) — confirmed zero real files, zero imports,
      never git-tracked; verified via app import + offline suite (376/3 deselected,
      unchanged). Live-verified the full dev stack still runs cleanly after (Postgres/
      Redis/uvicorn/arq/Vite from cold, browser round-trip against prior session data).
      **Found and fixed a real, previously-undetected CI bug**: user reported failing
      GitHub Actions commits; checked `github.com/.../commits/main/` directly (no `gh`
      auth needed) and found every commit since `40b3b58` ("feat: add contextual
      retrieval", 2026-07-29) had been failing `CI / test (push)` —
      `test_enrichment.py::test_contextual_embedding_enabled_reembeds_with_section_summary`
      asserted exact float equality between a pgvector-roundtripped embedding
      (single-precision `float4`) and a freshly-computed Python float64 value, which can
      never be exactly equal on a real Postgres round-trip. Fixed with `pytest.approx`.
      Verified green on GitHub Actions itself (`b6ebacc` → 2/2 checks passed), not just
      locally — the first time this project's CI status was checked directly rather than
      assumed from a local run. See memory.md for full detail, including a separate
      older (2026-07-16 to 07-21) failing-commit cluster found but not investigated
      (superseded by later clean commits, not currently blocking).

Next action: **the P0 roadmap from `research-production-agent-features.md` is fully
built, independently verified, and committed** as 6 commits on `main`
(`0cddb5e`→`3a54c25`, see memory.md "Wrap-up" for the full list + build technique).
Final baseline: backend 335 passed/3 skipped (started this round at 293), frontend
build clean with 161 passed (started at 156), ruff/`tsc -b`/`vite build` clean at
HEAD. **Not pushed to origin** — ask the user before pushing. A mid-process mistake
(destructive `git checkout` on a couple of test files, recovered by writing fresh
equivalent tests) is fully documented in memory.md, disclosed to the user at the
time, with zero impact on the actually-shipped application code. Ops
notes: add
`OPENAI_API_KEY`/`OPENAI_BASE_URL` (+ `*_MODE=real`, `STORAGE_MODE=local`) to
backend/.env before user-run real-seam dev sessions; `SEAMS_MODE`/`RLS_ENABLED` lines
in .env are dead and can be deleted; restart stale uvicorn/arq after backend edits.
**Resolved (2026-06-23):** `GET /context/docs` was deleted (decision: too risky to ship,
not org-scoped) — see buildplan.md "Unplanned additions".

## CSD358 IR hackathon (Track T1) — 2026-10-06 → 10-07 (all pushed, HEAD `0cfed4b`)
Prof approved submitting Veratas as-is. Work done via parallel subagents, each verified by the orchestrator.
- [x] From-scratch sparse IR core (positional inverted index, Porter, lnc.ltc, BM25, heap top-K, champion lists, idf elimination, zones, phrase/Boolean) as hybrid's lexical channel — `f3fafcc`
- [x] BEIR SciFact ablation harness + results (tfidf/bm25/zones/champions/dense/hybrid RRF; sparse-vs-dense analysis) — `f75c816`, `7e3bc34`, `f396858`
- [x] Per-sentence citation checker + Debug term-contribution view (migration 0025) — `e82c548`
- [x] QA user walkthroughs (core + team/admin) → `.claude/known-issues.md` tracker — `d97408c`
- [x] Usability fixes U1–U7, U10, U11 (repo table, folder delete, notebook rename, session expiry redirect, 403/404 notebook states, PDF-only uploads + friendly errors, history order, duplicate notice) — `cce1a83`, `9f60685`, `5b1f9b4`
- [x] Demo fixes D1–D4 (claim-check inheritance + calibration, broad-query/enrichment/overview enabled, page-accurate citation panel) — `ed9cdc3`
- [x] Markdown rendering in chat/overview/embed (D5) — `d63b766`
- [x] Citation checker measured on SciFact claims (AUC, P/R/F1, contradiction rate, evidence localization) — `4b466b1`
- [x] Search page Ranked/Boolean/Phrase modes with visible IR traces + `POST /retrieval/sparse-search` — `fa0fffa`, `126269c`
- [x] Visual polish: fixed-viewport shell, titles/favicon, sidebar name, Search polish (phrase snippets, Boolean validation, share bars, semantic pages) — `01c8245`, `4de678d`
Baselines: backend 493 passed / 3 deselected, frontend 224, ruff/tsc/build clean. Open items: `.claude/known-issues.md` (security S1–S4 deferred; F10 threshold decision; U8/U9/U12/U15–U18 low priority). Report + demo video not drafted (user's call).
