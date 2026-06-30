# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

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
- **F24 pipeline composed at `documents/router.py`**, not `documents.service`, to avoid a circular import (`ingestion.service` already imports `documents.service`).
- **Deterministic arq `job_id` (`f"ingestion:{stage}:{document_id}"`)** is the REAL concurrency guarantee against double-enqueue under redelivery — a before/after DB status check alone is NOT sufficient (two concurrent deliveries can both read the same "before" status in separate transactions before either writes).
- **F41 citations persist every call as a fresh Conversation + user Message + assistant Message** — no conversation reuse/multi-turn threading until a future feature builds history-threading alongside reuse (they must arrive together).
- **`resolve_allowed_documents(ctx)`** is the ONLY hook where V2 groups/grants permission logic slots in — MVP returns all org docs.
- **Chat module (`app/chat/`):** stateless was F40; F41 added models/repository (migration 0009). `chat/` is still flat (one cohesive pipeline, <200 lines each).

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
