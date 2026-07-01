# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

---

## MVC refactor cross-verification + commit (2026-07-01, this session)

**The MVC refactor is now COMMITTED (`6ff4be7`) and independently re-verified.** Before
committing, ran a swarm of 7 Haiku agents (Opus as orchestrator; all reads/writes done by
agents) auditing every slice against `docs/mvc-refactor-prompt.md`: models/schemas,
controllers/platform/entrypoints, services/repositories + both boundary rules,
exceptions/tasks/migrations/scripts, a repo-wide stale-import sweep, the frontend
model/controller/view split, and a docs self-audit. **Codebase verdict: fully correct
layer-first MVC — zero code issues.** Confirmed: no SQL outside repositories, cross-domain
calls via services only, the ingestion→documents one-way dep + controller-composed pipeline
circular-guard intact, universal `org_id` scoping via `BaseRepository._scoped()`, all 13
tables register, migration history untouched, no code-breaking stale references anywhere,
all old domain dirs + `src/features/` deleted.

- **Two doc-staleness fixes made** (only findings): (1) added a historical-path disclaimer
  block at the top of `progresstracker.md` (it lacked memory.md's disclaimer despite stale
  domain-first paths in its completed-feature entries) pointing to the old→new mapping table
  here; (2) corrected `architecture.md` `_parse_markdown_outline` reference from `seams.py`
  to `seams/real_parser.py` (seams is now a package). All other docs (codestandards,
  librarydocs, orchestrator, root CLAUDE.md, the refactor prompt itself) were already clean.
- **Green at commit time:** 135 backend tests vs real Testcontainers pgvector (1 real_parser
  deselected), 30/30 frontend vitest, `tsc -b`/`vite build` clean, ruff check clean except
  the 3 standing `scripts/inspect_document.py` findings, ruff format clean.
- **Gitignore hardened:** `.localstorage/` (LocalDiskObjectStore dev blobs), `.playwright-mcp/`,
  and `/pdf/` (5MB copyrighted `kech104.pdf` — manual-validation asset, never a fixture) are
  now ignored, NOT committed. `docs/` (mvc-refactor-prompt.md + er-diagram.md) IS committed as
  the in-repo historical record. Commit is on `main` (consistent with the whole project history).

---

## MVC layout refactor (2026-07-01, this session)

**Pure structural refactor — zero logic/schema/API change.** Backend went from
domain-first (`app/identity/`, `app/documents/`, `app/ingestion/`, `app/knowledge/`,
`app/retrieval/`, `app/chat/`) to layer-first MVC: `app/models/`, `app/schemas/`,
`app/controllers/`, `app/services/`, `app/repositories/`, `app/exceptions/`,
`app/tasks/`. Frontend went from `src/features/` to `src/models/` (types split out of
`lib/api.ts`), `src/controllers/` (api namespaces split out of `lib/api.ts`), and
`src/views/` (former `features/**` components, same relative depth to `lib/`/
`components/`). Full mapping: `docs/mvc-refactor-prompt.md` §3 (still in the repo as
the historical spec). **Every `app/<domain>/...` and `src/features/...` path
referenced anywhere below in this file is STALE** — see the table for the new
location. Old domain dirs and `src/features/` are fully deleted.

| Old (domain-first) | New (layer-first) |
|---|---|
| `app/<domain>/models.py` | `app/models/<domain>.py` |
| `app/<domain>/schemas.py` | `app/schemas/<domain>.py` |
| `app/<domain>/router.py` | `app/controllers/<domain>.py` (knowledge→`notebooks.py`) |
| `app/<domain>/service.py` (or `service/`) | `app/services/<domain>.py` (or `services/<domain>/`) |
| `app/<domain>/repository.py` (or `repository/`) | `app/repositories/<domain>.py` (or `repositories/<domain>/`) |
| `app/<domain>/exceptions.py` | `app/exceptions/<domain>.py` |
| `app/identity/{tokens,passwords,constants}.py` | `app/platform/{tokens,passwords,constants}.py` |
| `app/identity/deps.py` | `app/controllers/deps.py` |
| `app/ingestion/tasks.py` | `app/tasks/ingestion.py` |
| `app/documents/status.py` (`DocumentStatus`) | merged into `app/models/documents.py` |
| `src/features/<area>/*` | `src/views/<area>/*` |
| types in `src/lib/api.ts` | `src/models/{auth,documents,knowledge,chat}.ts` |
| api namespaces in `src/lib/api.ts` | `src/controllers/{auth,documents,notebooks,chat}Controller.ts` |

- `documents/service`, `documents/repository`, `ingestion/service` KEPT their
  subpackage shape (just moved: `services/documents/`, `repositories/documents/`,
  `services/ingestion/`) — package-layout convention untouched, only the parent
  namespace changed.
- Each new top-level package (`models/`, `schemas/`, `controllers/`, `services/`,
  `repositories/`, `exceptions/`, `tasks/`) got an `__init__.py` that re-exports
  everything from that layer — cross-domain imports still work via either the
  specific submodule or the package root.
- `main.py`/`worker.py`/`migrations/env.py` import lists updated to the new paths;
  `migrations/versions/*.py` untouched (immutable history).
- All fake/offline tests, `ruff check`/`ruff format --check` (only the 3 pre-existing
  `scripts/inspect_document.py` findings remain), every new-path import smoke test,
  `Base.metadata` table registration (13/13 tables), and `GET /health` all passed.
  Frontend: 30/30 vitest, `tsc -b` clean, `vite build` clean.
- **Docker-gated verification CLOSED (2026-07-01, same session, follow-up):** initial
  pass had Docker Desktop refuse to launch in-sandbox (93 Testcontainers-Postgres tests
  skipped, `pytest`: 43 passed / 93 skipped). Docker came up on retry; full suite re-run
  against real Postgres: **135 passed, 1 skipped** (the skip is the opt-in `real_parser`
  test, needs a live `OPENROUTER_API_KEY` — unrelated to Docker). Confirms the refactor
  is a true zero-logic-change: every previously-skipped DB-backed test now passes
  unchanged. `ruff check .` re-confirmed still exactly the 3 pre-existing findings.

---

## Real-embedder retrieval validation (2026-07-01, this session)

**Ask:** verify embeddings + retrieval work correctly with the real API key now in
`.env`, then exercise retrieval across documents with different section-hierarchy
shapes and fix the codebase if anything was wrong. **Result: retrieval/grounding is
fully correct; nothing in the codebase needed fixing.** One real, load-bearing finding
about heading recovery — corrected a stale claim in `progresstracker.md`'s F23 entry.

- **Real key wiring note:** `.env` only had `OPENROUTER_API_KEY` set. `RealEmbedder`/
  `RealLLM` (`app/platform/seams/real_llm.py`) read `OPENAI_API_KEY`/`OPENAI_BASE_URL`
  — a SEPARATE config pair from `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL` (used only by
  `RealParser`). Ran with `OPENAI_API_KEY=<the OpenRouter key>` and
  `OPENAI_BASE_URL=https://openrouter.ai/api/v1` set as process env (not written to
  `.env`) — same one-key-feeds-all-3-seams pattern as F40's manual gate, just made
  explicit here since it silently FAILED (`FAILED`/`EMBEDDING`, "OPENAI_API_KEY is not
  set") the first attempt.
- **Test method:** in-process `httpx` + `ASGITransport` against `main.app` (same pattern
  as `tests/test_real_parser_integration.py`), `FakeJobQueue` override to drive ingestion
  stages manually, `STORAGE_MODE=local` (no R2 creds). One notebook, multiple documents,
  real `/documents/upload` → `/ingestion/.../parse|structure|embed` → `/notebooks/{id}/
  documents/{doc_id}` → `/retrieval/search` + `/chat/ask`. Scripts were throwaway
  (scratchpad only, never committed, per the F51 precedent for manual validation
  scripts).
- **Generated 3 synthetic PDFs via reportlab** with deliberately different heading
  shapes (flat/no-headings, shallow 2-level, deep 3-level with a repeated H2 name
  "Diet" under two different H1 parents "Lions"/"Tigers" — to stress path/content
  disambiguation) to test hierarchy variation in a controlled way.
- **FINDING — `cloudflare-ai` (OpenRouter's file-parser plugin) does not recover
  semantic headings for these documents; it only marks page boundaries.** All 3
  synthetic PDFs AND a re-run of the known-good `pdf/kech104.pdf` (36-page real
  textbook, previously recorded in `progresstracker.md`'s F23 entry as "39 sections,
  genuine 3-level tree") produced the IDENTICAL wrapper structure: `document.pdf >
  Metadata > Contents > Page N` (one leaf section per page, `heading='Page N'`)
  regardless of the actual document content or visual heading styling (bold/large
  font). Directly inspected the raw parsing artifact JSON for `kech104.pdf`: the real
  heading text ("4.1 KÖSSEL-LEWIS APPROACH...") IS present in the extracted text but
  fused directly into the surrounding paragraph with ZERO markdown or even whitespace
  separating it (`"...MOLE CULAR S T R U CTURE4.1 K◌SSEL-LEwiS AppROACH tOCHEMiCAL
  BOnDinGIn order to explain..."`) — there is no signal of any kind
  (`#`-markdown/bold/newline) for `_parse_markdown_outline`'s regex to detect. **This is
  a vendor/engine characteristic on two-column academic PDFs, not a parsing bug** — per
  the locked "never fabricate" design (`real_parser.py`), the code correctly does NOT
  invent heading structure that isn't signaled. The prior "genuine 3-level tree"
  description in `progresstracker.md` was corrected — it was technically 3 levels deep
  (root > Metadata/Contents > Page N) but never reflected the document's real
  chapter/subsection structure. **No code change made** — broadening the heading regex
  would not help (there is no alternate signal to detect) and inventing headings via
  NLP heuristics would violate the locked contract; this is a documentation fix, not a
  code fix.
- **Retrieval + grounded generation stayed fully correct despite the coarse (page-level)
  hierarchy** — confirmed this is because F31 retrieval keys off chunk-CONTENT
  embeddings, not section labels/paths, so hierarchy quality has no bearing on
  retrieval correctness at the MVP (flat) stage. Verified across two separate multi-doc
  notebook runs (real API key throughout):
  - 3-doc notebook (flat crocodile fact-sheet, shallow Acme-Corp-handbook-style,
    deep Lions/Tigers guide with the repeated "Diet" H2 name under different parents):
    every targeted question retrieved the correct document and correct fact, correctly
    disambiguating "Diet > Hunting Behavior" (lions) from "Diet > Preferred Prey"
    (tigers) purely via chunk-content embedding similarity — no section-path confusion
    even though both docs' sections collapsed to the same generic wrapper shape.
  - 2-doc notebook (flat crocodile doc + the real 36-page/110-chunk `kech104.pdf`):
    a chemistry question ("Kossel-Lewis approach... octet rule") correctly retrieved
    tight-distance (0.35–0.49) chunks from `kech104.pdf` with a rich, correctly-cited
    `[1][2][3][4]` grounded answer; the crocodile question correctly retrieved the flat
    doc instead (not confused despite both docs sharing a notebook); a follow-up
    chemistry question (octet-rule exceptions) also correctly grounded with citations.
  - Both runs: an out-of-scope question ("2022 FIFA World Cup") correctly triggered the
    exact refusal behavior ("I don't have that in the provided sources.", zero
    citations) even with multiple unrelated documents in the notebook — first time this
    was verified with >1 document present (F40's original gate used a single document).
- **Open question surfaced for future V2 hierarchical retrieval design:** if
  `cloudflare-ai` typically degrades to page-level granularity on real multi-column
  PDFs, V2's planned hierarchical retrieval will often only have "page" as its
  practical section granularity for this vendor/engine, not true chapter/subsection
  structure — worth a deliberate design conversation (try `mistral-ocr` engine instead?
  a different `PARSER_MODEL`? an LLM heading-detection post-pass?) before building V2,
  not something to solve reactively then.

---

## Current phase / what is done

**Phase 0–3 COMPLETE. Phase 4 COMPLETE. Phase 5 mostly done.**

| Feature | Commit | Notes |
|---------|--------|-------|
| F00–F04 Platform skeleton | `c35ee11` | Layout, DB, migrations, seams/fakes, CI |
| F05 LocalDiskObjectStore | `8c44c75` | Offline storage; `STORAGE_MODE=r2\|local` |
| F10 Auth + org | `8940dd1` | Signup/login/JWT/refresh/invite/roles |
| F11 Folders + tags | `c58a5e7` | Materialized path, CRUD, tag attach/detach |
| F12 Upload + dedupe | `0b44b9c` | sha256 checksum, unique(org_id,checksum), status=UPLOADED |
| F25 Folder move/rename/delete | post-F41 | Parent-pointer model; 3 delete modes; path cache rebuilt in-tx |
| F25-followup | — | `create_folder` duplicate check; partial index `uq_folders_org_root_name` (migration 0010) |
| F20 Parsing stage | `0277cfe` | Parser seam → artifact JSON in object store; UPLOADED→STRUCTURING |
| F21 Structuring stage | `5eecac5` | Section tree + chunks; degenerate-outline contract |
| F22 Embedding stage | `4598698` | Upsert embeddings; EMBEDDING→READY |
| F23 Real parser | `9e7f319`/`90285c2` | OpenRouter `cloudflare-ai` + `mistral-ocr` fallback; per-seam modes |
| F24 Ingestion auto-dispatch | post-F41 | 3 arq jobs chained; deterministic `job_id` dedup; composed at router |
| F30 Notebooks | `a85138e` | `knowledge_bases`/`knowledge_base_documents`; "Notebook" public name |
| F31 Flat retrieval | `c194b2b` | kNN in ingestion repo; `assemble_context` pure fn; ContextBlock shape |
| Pkg-layout refactor | 4 commits | `platform/seams/`, `ingestion/service/`, `documents/repository/`, `documents/service/` |
| F40 Grounded generation | `1572fa8` | Non-streaming `POST /chat/ask`; retry-on-transient only; manual gate PASSED |
| F41 Citations | migration 0009 | `parse_citation_markers` → `resolve_citations`; fresh chunk read (provenance round-trip); conversation+message persisted |
| F4x SSE streaming | `e0d67df` | `POST /chat/stream`; `ChatService.stream_ask` async generator; no mid-stream retry |
| F52 Notebook + Chat UI | `e0d67df` | NotebookList/NotebookPage/ChatPanel/CitationPanel; fetch+ReadableStream SSE; 30/30 frontend tests |

**Remaining: F42 (debug bundle), F60 (RLS).**

---

## Locked design decisions (do not relitigate)

- **Module boundary rule:** a module calls another module ONLY through its `service` — never its `repository` or ORM models. Cross-module reads go through a narrow, purposeful `service` accessor.
- **No SQL outside `repository.py`. No business logic in `router.py`.**
- **Every DB query scoped by `org_id`** — both at the service/repo call AND as an independent backstop inside the repo method.
- **External services (LLM, embedder, parser) only through a seam interface.**
- **Ingestion stages are idempotent + resumable.** F20/F21 use status-eligibility check + delete-then-rebuild; F22 uses true upsert.
- **Package-layout convention (locked):** promote a layer file to a subpackage ONLY when >200 lines AND 2+ independent responsibilities — never speculatively. `retrieval/` having no `models.py`/`repository.py` is CORRECT (it owns no table).
- **Seam retry discipline:** broad `except Exception` is fine at a terminal stage boundary (stage just goes to FAILED). Inside a retry loop, catch ONLY `(TimeoutError, SeamTransientError)` — never broad-catch inside a retry (F40 precedent).
- **`dependency_overrides` shim CANNOT unblock the full upload→READY walk** because the arq worker never sees it. Storage impl must be config-selected (`STORAGE_MODE`), not DI-overridden per-process.
- **`ObjectStore` port is exactly `put` + `get`; no `exists`/`delete`.** Delete rides with the future orphan-sweep feature.
- **`path` on Folder is a non-authoritative DISPLAY CACHE**, rebuilt synchronously in-tx on every move/rename via `_rebuild_subtree_paths` (derives path from parent_id+name only — never slices the old path string, which would risk false-matching a sibling sharing a name prefix like "HR" vs "HR-Archive").
- **F24 pipeline composed at `app/controllers/documents.py`** (was `documents/router.py` pre-MVC-refactor), not `app.services.documents`, to avoid a circular import (`app.services.ingestion` already imports `app.services.documents`).
- **Deterministic arq `job_id` (`f"ingestion:{stage}:{document_id}"`)** is the REAL concurrency guarantee against double-enqueue under redelivery — a before/after DB status check alone is NOT sufficient (two concurrent deliveries can both read the same "before" status in separate transactions before either writes).
- **F41 citations persist every call as a fresh Conversation + user Message + assistant Message** — no conversation reuse/multi-turn threading until a future feature builds history-threading alongside reuse (they must arrive together).
- **`resolve_allowed_documents(ctx)`** is the ONLY hook where V2 groups/grants permission logic slots in — MVP returns all org docs.
- **Chat (`app/services/chat.py` + `app/repositories/chat.py` + `app/models/chat.py`, was `app/chat/` pre-MVC-refactor):** stateless was F40; F41 added the repository/models (migration 0009). Each layer file stays flat (one cohesive pipeline, <200 lines each) — the MVC refactor only changed which top-level package each file lives under, not this internal shape.

---

## Gotchas (things that will bite again)

- **Testcontainers + Docker Desktop/Windows:** Ryuk reaper flakes → `TESTCONTAINERS_RYUK_DISABLED=true` in conftest + CI. Docker daemon needs ~30–60s before serving after launch.
- **`SET LOCAL app.org_id = :bind` is INVALID Postgres.** Use `SELECT set_config('app.org_id', :org, true)` (third arg `is_local => true`, accepts bind params). `SET LOCAL` takes a literal token only.
- **`onupdate=func.now()` + immediate `model_validate` raises `MissingGreenlet`** inside an async session. Set `updated_at = datetime.now(UTC)` explicitly in the repository `update` method instead.
- **Shared Testcontainers DB across test files:** `pg_url` is session-scoped. Each test file MUST use a unique email prefix (e.g. `docs-`, `kg-`) or it collides with other files' signups.
- **Object-store test fixture must be a shared instance, not a fresh lambda.** `app.dependency_overrides[get_object_store] = lambda: _InMemoryObjectStore()` creates a NEW empty store per request. Hoist the instance: `store = _InMemoryObjectStore(); overrides[...] = lambda: store`.
- **Tests calling `ingestion_service.search_chunks` directly** (bypassing the HTTP `client` fixture) MUST depend on `tenant_engine` fixture, not just `session_factory`, or they hit the dev DB URL instead of the test container.
- **`uq_folders_org_parent_name` UNIQUE constraint does NOT protect root-level names** (`parent_id IS NULL` → Postgres NULL≠NULL). The partial index `uq_folders_org_root_name ON folders(org_id, name) WHERE parent_id IS NULL` (migration 0010) is the backstop. Both checks need to exist in `create_folder` — the app-level check and the `IntegrityError → FolderNameConflict` translation.
- **`FakeLLM` is context-aware:** checks whether the latest user-turn contains `"[1]"` — streams the grounded template if so, the fixed refusal string if not. Tests that want grounded-LLM behavior must send a context-bearing prompt.
- **F40 manual acceptance gate finding:** `FakeEmbedder` cannot prove a true-positive grounded answer (hash embeddings are semantically meaningless → retrieval distances ~0.93 regardless). Any future manual validation that needs to confirm a CORRECT grounded answer (not just refusal) must use `RealEmbedder`.
- **OPENROUTER_API_KEY serves all 3 seams** — parser (`cloudflare-ai`/`mistral-ocr`), embedder (`text-embedding-3-small`), LLM (`openai/gpt-4o-mini`) — all via `OPENAI_BASE_URL=https://openrouter.ai/api/v1`. One credential, three independently-metered paths.
- **Known gap (not fixed):** a lost enqueue — at upload time OR between stages — silently strands a document at whatever status it last reached. No sweeper/re-dispatch exists. Mid-chain stage can succeed in DB while its own next-stage enqueue fails; redelivery then sees "already past this stage" and skips the enqueue. Any future sweeper must account for BOTH the upload-time and the mid-chain failure points.
- **`chat/` `generate_answer` is an async-generator core.** F4x SSE switch was router-only (consume incrementally instead of to-completion) — confirmed correct in F4x: `stream_ask` in `service.py` is a new method, the router just iterates and yields SSE lines. No change to `generate_answer` or `call_llm_with_retry`.
- **Local `FileNotFoundError` vs boto3 `ClientError` parity** in `ObjectStore.get`: only safe because `parsing.py:65-68/78` and `structuring.py:217` all catch `except Exception` broadly. If any of those catches is EVER narrowed to a store-specific exception, cross-store parity silently breaks.
- **`scrollIntoView` not a function in jsdom**: jsdom creates real DOM elements but doesn't implement `scrollIntoView`. Calling `ref.current?.scrollIntoView(...)` when the method is `undefined` throws. Fix: `ref.current?.scrollIntoView?.(...)` — optional-chain the method call itself, not just the object.
- **`citations === undefined` vs `citations === []` as streaming sentinel**: in `ChatMessage`, `citations?: ResolvedCitation[]` being `undefined` = still streaming (don't render clickable markers), defined (even `[]`) = final answer (render markers if present). Avoids a separate `isStreamingMessage` boolean but requires discipline — never set `citations: []` mid-stream.
- **`accumulated` local var for token capture**: in `ChatPanel.handleSubmit`, tokens are captured in a closure-local `accumulated` string and appended in `onToken`, then `onDone` receives the final answer. Using React state for accumulation risks stale-closure reads when the functional update interleaves — the local var is simpler and correct.
- **SSE: no mid-stream retry in `stream_ask`**: once tokens are flowing the client has partial output; a restart would confuse the user. `stream_ask` has no retry loop — any error after the first token yields `{"type":"error"}`. Pre-token failures (retrieval/embedding) do propagate because no token has been sent yet.
- **`chatApi.streamAsk` is callback-based, not async-generator**: returns `() => void` cleanup immediately. Component stores cleanup in `abortRef.current` and calls it on unmount + new submission. This plays better with React's `useEffect` cleanup model than an async generator would.

---

## Schema quick-reference (what exists)

| Table | Migration | Key columns |
|-------|-----------|-------------|
| organizations | 0001 | id, name |
| users | 0001 | id, org_id, email, role |
| folders | 0004 | id, org_id, parent_id, name, path |
| tags | 0004 | id, org_id, name |
| documents | 0005 (ALTER of 0004) | id, org_id, folder_id, title, storage_key, checksum, status, failed_stage, error_detail, metadata_ |
| document_tags | 0004 | document_id, tag_id, org_id |
| sections | 0006 | id, org_id, document_id, parent_section_id, heading, level, char_start, char_end, page_start, page_end, path, ordinal |
| chunks | 0006 | id, org_id, document_id, section_id, char_start, char_end, content, token_count, ordinal |
| embeddings | 0007 | id, org_id, document_id, owner_type, owner_id, model, dim, embedding vector(1536) |
| knowledge_bases | 0008 | id, org_id, name, description |
| knowledge_base_documents | 0008 | knowledge_base_id, document_id, org_id |
| conversations | 0009 | id, org_id, created_at |
| messages | 0009 | id, org_id, conversation_id, role, content, citations jsonb, created_at |

**Next migration: 0011** (message_traces for F42).

---

## Open questions / future decisions

- Reranker (4th seam) — add when real quality complaints arise in V2.
- F42 `message_traces` schema — needs raw_prompt, raw_output, hit scores, latency_ms; admin-read-only.
- F52 SSE consumption RESOLVED: `fetch` + `ReadableStream.getReader()` + `TextDecoder`; buffer splits on `\n\n` to handle partial reads; `AbortController` in `useRef` for cleanup on unmount/re-submit. `EventSource` was NOT used (POST body required).
- V2 folder-permissions: per-folder role-based access (client stated as a real future need) — `folder_id` is already the stable FK anchor; no permission code exists yet.
- Orphan blob sweep — `ObjectStore.delete` not built (deliberately deferred, rides with the sweep feature).
