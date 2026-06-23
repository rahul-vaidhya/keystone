# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

## Current phase
**Phase 0 COMPLETE** (F00–F04, F03+F04 = c35ee11, 27 tests, ruff clean). **Phase 1 (Identity +
Documents) COMPLETE**: F10 (`8940dd1`), F11 (`c58a5e7`), F12 (`0b44b9c`). **F50 + a slice of F51
(Phase 5 frontend) DONE and committed** (`054aa36`). **Phase 2: F20 parsing stage DONE** (`0277cfe`)
**and F21 structuring stage DONE** (`5eecac5`, this session). Next: F22 embedding stage (embed
chunks, upsert `embeddings(owner_type='chunk')`, status→ready), or resume the rest of F51
(folders/tags/upload UI) against the real F11/F12 backend — ask the user which.

## F21 Structuring stage (2026-06-25, this session, 5eecac5)
- **New tables owned by `ingestion`, not a new module**: `app/ingestion/models.py`
  (`Section`, `Chunk`) + `app/ingestion/repository.py` (`SectionRepository`/
  `ChunkRepository`, each just `delete_for_document` + `bulk_create`). Migration
  `0006_sections_chunks.py`. Decision: ingestion produces this structural data and no
  other module exists yet that needs to own it; a future retrieval module (F31) can import
  these ORM classes directly for joins (same pattern `document_tags` already uses against
  `documents`) without violating the module-boundary rule, since that's not calling
  ingestion's service/repository methods.
- **Idempotency = delete-then-rebuild in one transaction, not row-level upsert.** On a
  (re)run, `run_structuring_stage` deletes the document's existing sections+chunks then
  inserts freshly-built ones, inside one `ingestion`-owned transaction — simpler than
  upserting a parent/child tree with stable IDs across reruns, and equally duplicate-free.
  `chunk_id` is still a deterministic `sha256(document_id|ordinal|content)` hash per
  codestandards ("Ingestion correctness") even though this code path doesn't rely on it for
  upsert-matching — it's there so a future move to true upserts needs no migration.
  Section ids are plain `uuid.uuid4()` (no determinism needed since the whole subtree is
  rebuilt together every time).
- **Tree-building algorithm**: `_build_section_nodes` in `app/ingestion/service.py` turns
  the parser's flat outline (`heading`, `level`, offsets — document order) into a nested
  tree via a stack keyed on `level`: deeper level → child of stack top; shallower/equal →
  pop until a lower-level parent is found. **Degenerate-outline contract** (architecture.md):
  empty outline → ONE root section (`heading=None`, `char_start=0`, `char_end=len(text)`),
  itself a leaf, so it still gets chunked — the invariant "every chunk has a `section_id`"
  holds by construction in both the headed and headingless case.
  - **Gotcha if this is ever revisited**: the algorithm doesn't validate that children's
    char ranges actually cover their parent's full range — it just trusts the outline.
    Chunking happens only on LEAF sections precisely to dodge the double-coverage problem
    this would otherwise cause; if a future parser ever emits a heading tree with real gaps
    between a parent's range and its children's, that gap's text is silently never chunked.
    Not handled — no real parser exists yet to produce that shape (FakeParser/F20 are both
    flat two-heading outlines).
- **Chunking**: leaf sections only, ~1000-char windows breaking on the nearest preceding
  space (`_split_into_windows`), `token_count = len(content) // 4` (a rough heuristic — no
  tokenizer dependency added; revisit if real chunk-size accuracy ever matters for cost/
  context-window tuning). `ordinal` is document-wide (not reset per section), increasing in
  document order, for stable future citation ordering.
- **`documents.service.get_parse_artifact_key(ctx, document_id) -> str`**: a narrow accessor
  added so `ingestion.service` can read the F20-written `metadata_["parse_artifact_key"]`
  without exposing the internal `metadata` column on the public `DocumentOut` HTTP response
  shape. Single caller today (ingestion's structuring stage) — deliberate, not a leftover.
- **`begin_structuring`/`complete_structuring`** added to `DocumentRepository` +
  `DocumentsService`, exactly mirroring F20's `begin_parsing`/`complete_parsing` shape:
  eligible to (re)start structuring only if status is `STRUCTURING` (the state F20 leaves a
  doc in, or a crashed-and-resumed run) or `FAILED` with `failed_stage == STRUCTURING`;
  anything `EMBEDDING`+ returns unchanged (idempotent no-op).
- **Audited for unused code**: removed `SectionRepository.list_for_document` and
  `ChunkRepository.list_for_document` before committing — written speculatively, ended up
  with zero callers (tests query `Section`/`Chunk` via a raw `select(...)` instead). Same
  audit habit as F20's removed speculative `DocumentsService.get_document`.
- 6 new integration tests in `tests/test_ingestion.py`: successful structuring (status →
  EMBEDDING), idempotent re-run (no duplicates, identical response), failure path (artifact
  missing/corrupt → FAILED + failed_stage=STRUCTURING), tenant isolation, sections/chunks
  shape assertions (every chunk's `section_id` in the document's section set, valid
  `char_start < char_end`), and the degenerate-outline case (one root section, all chunks
  attached to it). Full suite: 55/55 green, ruff clean.

## F20 Parsing stage (2026-06-24, this session, 0277cfe)
- **New `app/ingestion` module**: `service.py` + `router.py` only — deliberately **no**
  `repository.py` (ingestion owns no table of its own; every `documents` row mutation goes
  through `documents_service`, never `documents.repository` directly — the module-boundary
  hard rule), **no** `schemas.py` (reuses `documents.schemas.DocumentOut`), and **no**
  `tasks.py`/arq wiring — nothing enqueues a parse job yet (no caller), so building one now
  would be dead code. Wired the manual-trigger endpoint into `main.py`; production
  auto-dispatch (enqueue-on-upload, or F21 chaining a follow-on job) is left for whichever
  future feature actually needs it.
- **Zero migration needed.** F12 already added `page_count/language/status/failed_stage/
  error_detail/metadata` to `documents` — F20 only adds behavior, not schema.
- **New `DocumentRepository` methods** (`begin_parsing`/`complete_parsing`/`mark_failed`) and
  matching `DocumentsService` methods (`begin_parsing`/`complete_parsing`/`fail_stage`) — F20's
  ingestion module calls these instead of touching the `documents` table itself.
- **Idempotency/resumability lives in `DocumentRepository.begin_parsing`**: a document is
  eligible to (re)start parsing only if its status is `UPLOADED`, already `PARSING` (crashed
  before the artifact was persisted — nothing to resume, just re-parse), or `FAILED` with
  `failed_stage == PARSING` (retry). Anything already past parsing (`STRUCTURING`+) comes back
  unchanged with no seam/object-store calls — `IngestionService.run_parsing_stage` checks
  `document.status != PARSING` after calling `begin_parsing` and returns early if so.
- **`ObjectStore` Protocol gained a `get(key) -> bytes` method** (alongside the existing
  `put`) — F12 only ever wrote blobs; F20 is the first feature that needs to read one back
  to hand to the `Parser` seam. Added to `R2ObjectStore` too (one more `asyncio.to_thread`
  around the boto3 call).
- **Parser artifact format** (persisted to `org/{org_id}/doc/{document_id}/artifacts/
  parsing.json` via new `storage.build_artifact_key`): JSON `{text, language, page_count,
  outline: [{heading, level, char_start, char_end, page_start, page_end}, ...]}` — directly
  serializes `ParsedDoc`/`OutlineNode` from the seam. This is the contract F21 structuring
  reads from; do not change its shape without checking F21's consumer.
- **Failure handling**: `run_parsing_stage` wraps the object-store `get`/`put` + `parser.extract`
  calls in one `try/except Exception`, logs via structlog (`ingestion.parsing_failed`), then
  calls `documents_service.fail_stage(...)` — it does NOT re-raise, so the HTTP endpoint
  returns 200 with the document's `FAILED` state in the body rather than a 500. This matches
  codestandards "on failure: set status=failed, never swallow" (logged + persisted, not raised).
- **Gotcha — test object-store fixture was silently broken for round-tripping**: the existing
  `tests/test_documents.py` `client` fixture did
  `app.dependency_overrides[get_object_store] = lambda: _InMemoryObjectStore()` — a **new**
  empty store on every dependency resolution, since FastAPI calls the override fresh per
  request. F12's tests never noticed because dedupe is checked via the DB checksum, not the
  store. F20 needs to `put` (upload) then `get` (parse) the *same* blob across two separate
  HTTP requests, which only works with one shared instance — fixed by hoisting `store =
  _InMemoryObjectStore()` outside the lambda in that fixture, and using the same pattern in
  the new `tests/test_ingestion.py`. **Any future test that exercises object-store
  round-tripping across requests must use this hoisted-instance pattern, not a fresh lambda.**
- 4 new integration tests in `tests/test_ingestion.py` (successful parse incl. artifact
  content check, parser failure via a `get_parser` dependency override, idempotent re-run
  returns an identical response, cross-org parse attempt 404s). Full suite: 49/49 green,
  ruff clean. Audited for unused code: removed a speculative `DocumentsService.get_document`
  that had no caller — kept the audit trail here so it isn't silently re-added later.

## F12 Upload + checksum dedupe (2026-06-24, this session, 0b44b9c)
- **ALTERed the F11 `documents` anchor table** (migration `0005_document_upload_dedupe.py`) exactly
  per the F11 entry's pre-recorded plan: `storage_key/checksum/mime_type/byte_size/page_count/
  language/status/failed_stage/error_detail/metadata`, `unique(org_id, checksum)`. No new table.
- **`metadata` Python attribute is named `metadata_`** (mapped to the `"metadata"` DB column) —
  `metadata` is reserved on SQLAlchemy's `DeclarativeBase` (collides with `Base.metadata`).
- **New `app/documents/status.py`**: the authoritative `DocumentStatus` `StrEnum`
  (`UPLOADED|PARSING|STRUCTURING|EMBEDDING|READY|FAILED`) architecture.md names as the single
  source of truth — built in full now even though F12 only uses `UPLOADED`, since the enum itself
  (not its later transitions) is the locked schema decision.
- **New `app/platform/storage.py`**: `ObjectStore` Protocol + `R2ObjectStore` (lazy `import boto3`
  inside `__init__`, mirrors the seam adapters' lazy-import pattern) + `get_object_store()` FastAPI
  dependency factory + `build_storage_key(org_id, document_id, filename)` →
  `org/{org_id}/doc/{document_id}/source{ext}` (librarydocs.md convention).
  **This is deliberately NOT a 4th seam** — architecture.md says only Parser/Embedder/LLM are
  seams; the object store is called directly. Testability comes from plain FastAPI
  `app.dependency_overrides[get_object_store]`, not a `SEAMS_MODE`-style fake/real switch.
- **Added `boto3` and `python-multipart` as core `pyproject.toml` dependencies** (not under the
  `[real]` extra like the OpenAI seam) — unlike the seams, the object store has no fake/real mode;
  production always needs it, and tests override the FastAPI dependency instead of swapping a
  config flag. `python-multipart` is required by FastAPI for `UploadFile`/`Form` parsing.
- **Dedupe is check-then-act, not concurrency-safe** (`DocumentsService.upload_document`): looks up
  `get_by_checksum` before inserting, inside one transaction. The `unique(org_id, checksum)`
  constraint exists as a backstop, but a true concurrent double-upload of the same byte-identical
  file in the same org could still raise `IntegrityError` on commit instead of returning the
  existing row — **not handled** (no retry/catch). **Minor, deliberately left**: the DoD only
  requires sequential re-upload to dedupe, and this is a narrow race window; revisit if it's ever
  observed in practice rather than building speculative concurrency handling now.
- **Object-store-write-before-DB-commit ordering**: `upload_document` flushes the row (to get its
  `id` for the storage key) but does NOT commit until after `object_store.put()` succeeds — if the
  put fails, the whole transaction (including the row) rolls back, so there's never a DB row
  pointing at a blob that was never written. (The reverse leak — a blob written but the transaction
  then failing for an unrelated reason — is accepted; same class of gap the orphan sweep design in
  librarydocs.md's "Object storage" section exists to catch later.)
- **Upload response status code**: `201` on first upload, `200` when the checksum already existed
  (dedupe hit) — the router returns a `JSONResponse` directly rather than using `response_model`
  because the status code is data-dependent.
- 4 new integration tests in `tests/test_documents.py` (create + `status=UPLOADED`, re-upload
  returns existing doc, same-checksum-different-orgs not deduped, missing-folder 404), using a
  `_InMemoryObjectStore` test double registered via `app.dependency_overrides[get_object_store]`.
  Full suite: 45/45 green, ruff clean.

## F11 Folders + tags (2026-06-23, this session, c58a5e7)
- **New `app/documents` module** (first module besides `identity`): `models.py` (`Folder`,
  `Tag`, `Document`, `DocumentTag`), `repository.py`, `service.py`, `router.py`,
  `schemas.py`, `exceptions.py`. Migration `0004_folders_tags.py` (Revises `0003`).
- **`documents` table is deliberately minimal in this migration** — only
  `id/org_id/folder_id/title/created_at`. It exists now only so `document_tags` has
  something to FK to (folders/tags need a document to attach to for the DoD's "tag a
  document"). **F12 ALTERs this same table** to add `storage_key/checksum/mime_type/
  byte_size/page_count/language/status/failed_stage/error_detail/metadata` — F12 does
  **not** get a new table. Don't recreate `documents` in F12's migration.
  - **Why:** buildplan sequences F11 before F12, but F11's DoD ("tag a document") needs a
    document row to exist. Building the full upload/dedupe columns now would be doing F12's
    job out of order; building nothing would leave document_tags with no FK target. The
    minimal-anchor-table-now / ALTER-later split is the smallest move that respects both
    constraints, mirroring the project's existing "structural now, semantic later" pattern.
- **Folders**: self-referencing tree, `path` materialized column (e.g. `'HR/Policies'`),
  built by reading the parent's `path` at create time (`f"{parent.path}/{name}"`, or just
  `name` for a root folder). `ON DELETE CASCADE` on `parent_id` (delete cascades to
  subtree) and on `documents.folder_id` it's `SET NULL` (folder is "not a permission
  boundary" per architecture.md — deleting a folder must not delete its documents).
- **Scope decision — no folder rename/move endpoint**: buildplan's one-line feature
  description says "CRUD for the folder tree," but the actual DoD line only requires
  "create nested folders; tag a document; list by folder/tag." Implemented Create/Read/
  Delete for folders and tags; deliberately **did not** build folder rename/move, because
  a move requires rewriting the materialized `path` of every descendant (a cycle-detection
  + bulk-update routine) that the DoD doesn't exercise and that risked introducing
  untested bugs. **If a future feature needs folder move, build it then** — this was a
  scope call, not an oversight; noted here so it isn't silently re-litigated as "missing
  CRUD."
- **Tag attach/detach is idempotent**: `attach` uses `INSERT ... ON CONFLICT DO NOTHING`
  (Postgres dialect insert) on `(document_id, tag_id)`; `detach` is a no-op if the row
  doesn't exist. `create_tag` is get-or-create by `(org_id, name)` rather than erroring on
  duplicate — there's a unique constraint on `(org_id, name)` so this avoids a 409 for the
  common case of re-tagging with an existing tag name.
- **Gotcha — cross-test-file data collisions in the shared Testcontainers DB**: `pg_url` is
  a `scope="session"` fixture, so **one Postgres container is shared across every test file
  in the run**, and `AuthRepository.email_exists_globally` checks across the WHOLE
  database, not per-test. `test_documents.py`'s first draft reused emails already used in
  `test_auth.py` (`owner2@test.com` through `owner5@test.com`) and got `409 Conflict` on
  signup. Fixed by prefixing all emails in `test_documents.py` with `docs-`. **Any new test
  file that signs up users must use an email prefix/namespace unique to that file** — this
  is a standing constraint of the test harness, not a one-off bug.
- Exception handlers added to the existing `app/platform/http.py` (not a new file) for
  `FolderNotFound`/`TagNotFound`/`DocumentNotFound` (404) and the `DocumentsError` base
  (400) — same pattern as identity's handlers in the same file.
- 7 new integration tests in `tests/test_documents.py` (nested folder creation + path
  correctness, missing-parent 404, folder delete, tag-a-document + list-by-tag + detach,
  list-by-folder, tag-missing-document 404, two-org tenant isolation on folders/tags).
  Full suite: 41/41 green, ruff clean. No unused code or single-caller-abstraction issues
  found on audit — every repository/service method has a real caller.

## `/context/docs` removed (2026-06-23, this session)
- The unplanned/undecided `GET /context/docs` endpoint (see prior entry below) was **deleted**,
  not formalized — decision: too risky to ship (served `.claude/`/`CLAUDE.md` to any authenticated
  user across all orgs, not org-scoped).
- **Why:** direct senior instruction to remove it before committing F10/F50/F51, rather than
  carry an undecided cross-tenant-readable endpoint into the committed history.
- **Removed:** `backend/app/platform/context_docs.py`, the router import/mount in `main.py`,
  `CONTEXT_DOCS_ROOT` from `platform/config.py`, `test_context_docs_list` from `test_auth.py`;
  frontend `DocsPage.tsx` + `MainPanels.tsx`, `contextApi`/`DocEntry`/`DocContent` from
  `lib/api.ts`, the `/app/docs` route in `App.tsx`, the `Library` nav item in `Sidebar.tsx`, the
  `/context` Vite proxy entry, and the now-unused `react-markdown` dependency (package.json +
  regenerated package-lock.json). Auth UI and the plain app shell (Home, Users, AppShell,
  Sidebar) were kept as-is.
- **Verified:** `pytest` 34/34 green (Testcontainers Postgres, Docker Desktop had to be started
  first), `ruff check` clean, frontend `tsc -b` clean with no dangling references.
- **Committed as two commits**: `8940dd1` "F10: auth backend" (identity/*, migration 0003, auth
  tests, pyproject auth deps, main.py wiring, docker-compose port fix, the context_docs deletion
  bits that live in those same files) and `054aa36` "F50/F51 (partial): frontend auth UI + app
  shell, built ahead of sequence per direction" (Vite scaffold + auth UI, with the docs-viewer
  already stripped out before staging).
- `buildplan.md`'s "Unplanned additions" entry for this endpoint is marked RESOLVED: deleted.

## Re-baseline correction (2026-06-22, this session — docs only, no code)
- **Why this was needed:** `memory.md`/`progresstracker.md` still said "Next: F10" while F10
  (full auth backend) and F50 + an auth-adjacent slice of F51 (frontend app shell + auth UI) were
  already built, uncommitted, on disk. Re-baselined both files against actual repo state, not
  against the stale plan.
- **F10 DONE:** `app/identity/{router,service,repository,models,schemas,deps,tokens,passwords,
  constants,exceptions}.py` + migration `0003_auth_password_hash` + `tests/test_auth.py` (217
  lines). Signup creates org+owner; login is multi-org aware; refresh/logout/`/me`/invite/list
  users/patch role all present.
- **F50 DONE + F51 PARTIAL, built OUT OF SEQUENCE per direct senior instruction** (ahead of Phase
  2–4): Vite React scaffold (`frontend/{package.json,vite.config.ts,...}`), `App.tsx`/
  `ProtectedRoute`/`lib/auth.tsx`/`lib/api.ts`, `AppShell`/`Sidebar`/`HomePage` (F50), plus
  `LoginPage`/`SignupPage` wired to the real F10 endpoints and `UsersPage.tsx` (org user/role
  management) + a `DocsPage.tsx` placeholder (auth-adjacent slice of F51). Folders/tags/upload UI
  itself is **not started** — don't resume it until F11/F12 land on the backend.
- **F03/F04 status corrected:** were briefly suspected stale/skipped during this re-baseline, but
  verified still fully intact and unchanged since `c35ee11` (`git diff c35ee11 HEAD` empty for
  `seams.py`/`ci.yml`) — confirmed with the user, no actual gap. Don't re-litigate this.
- **`GET /context/docs`** — was unplanned/undecided as of the re-baseline; resolved and deleted
  this session, see the entry above.

## Phase 0 build decisions (F03 seams + F04 CI, 2026-06-22, c35ee11)
- **All 3 seams live in ONE flat module `platform/seams.py`** (matches the flat platform/ layout —
  config.py, db.py, etc.), not a package. Holds: `Parser`/`Embedder`/`LLM` `@runtime_checkable`
  Protocols; shared frozen-dataclass types `ParsedDoc`/`OutlineNode`/`Message`; fakes; real adapters;
  the factory; `SeamNotConfigured`.
- **Fakes are the product default everywhere** (`SEAMS_MODE=fake`): `FakeEmbedder` = deterministic
  unit-norm vector seeded from `int(sha256(text))` → reproducible retrieval asserts; `FakeLLM` streams
  a templated grounded answer citing `[1]`; `FakeParser` returns fixed text + a 2-node outline with
  real char offsets (so F21 structuring can run with no PDF).
- **`Embedder` Protocol exposes `model` + `dim` properties** (not just `embed`) — `model` is stamped
  onto `embeddings.model`, the retrieval filter that stops duplicate hits after a re-embed. `EMBED_DIM
  = 1536` constant ties fake + real to the `vector(1536)` column. `LLM.stream` is declared as a plain
  `def -> AsyncIterator[str]` (async-generator-compatible), matching `async def ... yield` impls.
- **Real adapters are behind the seam and config-gated, NOT prematurely committing vendors:**
  `RealEmbedder`/`RealLLM` target an OpenAI-compatible API (defaults `text-embedding-3-small` /
  `gpt-4o-mini`, both in config, swappable), **lazy-import `openai`** inside a `_openai_client()` helper
  and raise `SeamNotConfigured` if key/SDK missing → the fake-only suite needs neither. `openai` is an
  **optional `[real]` extra** in pyproject (NOT in `[dev]`/default), so CI/tests install nothing extra.
  `RealParser` is a **Phase-2 (F20) stub** that raises `SeamNotConfigured` — the OCR vendor is a locked
  deferral, so building a "real" parser now would violate that decision (noted as the one Minor).
- **Factory** `get_parser/get_embedder/get_llm()` switches on `settings.SEAMS_MODE` (`fake`|`real`),
  rejects unknown modes with `SeamNotConfigured`. Features inject the returned object (DI) so tests pass
  a fake. No seam is wired into a feature yet — first consumer is ingestion (Phase 2).
- **F04 CI** = `.github/workflows/ci.yml` (first workflow in the repo): on push + PR, ubuntu-latest
  (ships Docker so Testcontainers actually runs, no skip), `working-directory: backend`, `pip install
  -e .[dev]`, `ruff check . && ruff format --check .`, then `pytest -q`. `TESTCONTAINERS_RYUK_DISABLED=
  true` set as a job env (carried from the F02 gotcha). Seams stay on fakes → no API keys in CI.
- **F03 tests are pure unit** (`tests/test_seams.py`, 12 tests): determinism, vector width == dim,
  protocol conformance (incl. real adapters), factory switching, unknown-mode rejection, and
  real-adapter-fails-loudly (`RealEmbedder.embed` w/o key, `RealParser.extract`). No DB, no keys.
- **Pre-existing Minor (not introduced here):** a `StarletteDeprecationWarning` (httpx vs testclient)
  surfaces in the suite — unrelated to F03/F04, left for a deps-hygiene pass.

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
