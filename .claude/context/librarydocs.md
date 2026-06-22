# librarydocs.md — How THIS Project Uses Its Libraries

Project-specific patterns and our own wrappers. Not generic docs — for generic API usage,
web-search the current official docs (versions move). Focus here is "the way we do it."

## FastAPI
- One `TenantContext` dependency resolves the authenticated user → `org_id`, injected everywhere.
  ```python
  async def get_ctx(user = Depends(current_user)) -> TenantContext:
      return TenantContext(org_id=user.org_id, user_id=user.id, role=user.role)
  ```
- Routers are thin (see codestandards). One global exception handler maps domain errors → HTTP.
- OpenAPI at `/docs` is free; the SPA uses a hand-written typed fetch wrapper (no SDK codegen).

## SQLAlchemy (async) + Alembic
- Async engine + `async_sessionmaker`; session injected per request. Repositories take the session.
- Migrations via Alembic; **never** edit a shipped migration — add a new one.
- **One session helper for everything — `tenant_session(org_id)` — used by BOTH the request path
  and arq workers** (see architecture.md "Tenancy plumbing"):
  ```python
  @asynccontextmanager
  async def tenant_session(org_id):
      async with sessionmaker() as s, s.begin():
          if settings.RLS_ENABLED:                       # default FALSE in dev/test (MVP)
              await s.execute(text("SET LOCAL app.org_id = :org"), {"org": str(org_id)})
          yield s
  ```
  - Use **`SET LOCAL`** (transaction-scoped), never plain `SET` — plain `SET` persists on a pooled
    connection and leaks `org_id` into the next checkout.
  - Repositories **always** apply `WHERE org_id = :org` regardless of `RLS_ENABLED`. RLS is the
    backstop, the app filter is the guarantee.
- **RLS — DESIGNED NOW, ENABLED IN PHASE 6 (Security Hardening). Do not turn on in MVP.** The
  migration is written but the teeth are gated by `RLS_ENABLED`:
  ```sql
  -- created in a migration; takes effect only once the app connects as the restricted app_user role
  ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
  ALTER TABLE documents FORCE ROW LEVEL SECURITY;        -- Phase 6: applies even to table owner-ish roles
  CREATE POLICY tenant_isolation ON documents
    USING (org_id = current_setting('app.org_id', true)::uuid);   -- missing_ok → unset GUC = NULL = no rows
  ```
  - `organizations` has no `org_id`; its policy keys on `id = current_setting('app.org_id', true)::uuid`.
  - **Role split (Phase 6):** Alembic + tests run as a privileged **`migrator`** (owns tables, bypasses
    RLS); the running app connects as a restricted non-owner **`app_user`** that RLS actually constrains.
    In MVP everything runs as `migrator`, so RLS is inert even where the policy exists — which is why the
    MVP isolation test asserts the **app-level** filter, and the real teeth-having test arrives in Phase 6.

## pgvector
- Column `embedding vector(1536)`. **HNSW** index for ANN search.
- The MVP retrieval query (note the scope filter does the notebook + permission work):
  ```sql
  SELECT e.owner_id AS chunk_id, c.content, c.document_id, c.char_start, c.char_end,
         e.embedding <=> :qvec AS distance
  FROM embeddings e JOIN chunks c ON c.id = e.owner_id
  WHERE e.org_id = :org
    AND e.owner_type = 'chunk'
    AND e.model = :active_model                     -- REQUIRED: without it a re-embed under a new
                                                    -- model name returns duplicate hits per chunk
    AND e.document_id = ANY(:scope_document_ids)    -- notebook ∩ allowed
  ORDER BY e.embedding <=> :qvec
  LIMIT :k;
  ```
- `<=>` = cosine distance (smaller = closer). Keep the embedding model consistent between index & query.
- **Filtered-ANN rule** (the `document_id = ANY(...)` predicate restricts the candidate set):
  - **Small notebook scope** (≲ a few hundred docs): use **exact KNN** with the filter — accurate,
    and the filtered set is small enough that ANN buys little.
  - **Larger scope:** use the **HNSW** index with a raised `ef_search` so post-filtering still returns
    a full `k`. Filtered-recall tuning is flagged as a **V2 revisit**.

## arq (background workers)
- `worker.py` defines the task functions; enqueue from a service. Tasks are thin and call the
  module's `service`. Pass the `request_id` and `org_id` in the job payload for tracing + scoping.
- **A worker opens its DB work via `tenant_session(org_id)` using the `org_id` from the job payload**
  — the same helper the request path uses — so background writes are tenant-scoped exactly like
  request writes (no HTTP `TenantContext` required).
- Ingestion stages are separate task functions chained on success; each is idempotent so retries
  are safe and a `FAILED` doc resumes from its last good stage.

## The 3 seams (our wrappers, in `platform/`)
- Real adapters wrap the vendor APIs; **fakes** are the default in tests/local:
  - `FakeEmbedder`: deterministic vector from `sha256(text)` → reproducible retrieval assertions.
  - `FakeLLM`: streams back a templated answer citing the provided context → tests citation mapping.
  - `FakeParser`: returns a fixed text + outline → tests structuring without a real PDF.
- Swap real⇄fake via config; production wires real adapters, CI wires fakes.

## SSE streaming (chat)
- Chat endpoint returns `StreamingResponse` (or EventSourceResponse) yielding tokens from
  `LLM.stream(...)`. The SPA consumes via `EventSource`/fetch-stream. Citations are emitted/resolved
  after generation and persisted to `messages.citations`.

## Object storage
- Raw uploads keyed `org/{org_id}/doc/{document_id}/source.ext`; persisted artifacts under
  `.../artifacts/{stage}.json`.
- **Deletion (two stores, two mechanisms — the object store cannot join a Postgres tx):**
  1. **DB:** `ON DELETE CASCADE` chains, atomically inside the transaction. Deleting a `documents`
     row cascades to its `sections`, `chunks`, `embeddings`. (Separately, deleting a `conversations`
     row cascades `messages`→`message_traces`; conversations belong to a notebook, not a document.)
  2. **Object store:** S3 blobs are deleted by an **idempotent blob-deletion job** enqueued after the
     DB commit (re-running it is safe).
  3. **Safety net:** a periodic **orphan sweep** deletes any blob whose `document_id` no longer exists
     in the DB — so a crash between (1) and (2) self-heals. (There is no "same DB tx" for blobs.)

## Parser / OCR vendor
- Decided in Phase 2. Whatever it is, it stays behind the `Parser` seam. **Do not hand-roll OCR.**

## When unsure about a library's current API
- Check the seam/wrapper first (we may already encapsulate it). Then web-search the official docs for
  the version in `pyproject.toml`. Prefer our wrapper's pattern over a generic snippet.
