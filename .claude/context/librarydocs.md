# librarydocs.md — How THIS Project Uses Its Libraries

Project-specific patterns and our own wrappers. Not generic docs — for generic API usage,
web-search the current official docs (versions move). Focus here is "the way we do it."

## FastAPI
- One `TenantContext` dependency resolves the authenticated user → `org_id`, injected everywhere.
  Defined in `app/middleware/deps.py`.
  ```python
  async def get_ctx(user = Depends(current_user)) -> TenantContext:
      return TenantContext(org_id=user.org_id, user_id=user.id, role=user.role)
  ```
- Routes (`app/routes/<domain>.py`) wire paths only; controllers (`app/controllers/<domain>.py`)
  are thin (see codestandards). One global exception handler maps domain errors → HTTP.
- OpenAPI at `/docs` is free; the SPA uses a hand-written typed fetch wrapper (no SDK codegen).

## SQLAlchemy (async) + Alembic
- Async engine + `async_sessionmaker`; session injected per request. Repository classes (inside
  `services/<domain>/`) take the session.
- Migrations via Alembic; **never** edit a shipped migration — add a new one.
- **One session helper for everything — `tenant_session(org_id)` (in `app/config/db.py`) — used by
  BOTH the request path and arq workers** (see architecture.md "Tenancy plumbing"). It is the ONLY
  sanctioned way to open a session — a guard test in `tests/test_rls.py` fails the build on any
  bare `sessionmaker()` outside `config/db.py`:
  ```python
  @asynccontextmanager
  async def tenant_session(org_id):
      async with sessionmaker() as s, s.begin():
          await set_org_guc(s, org_id)      # ALWAYS — enforcement never depends on a flag
          yield s
  ```
  - Use **`set_config('app.org_id', :org, true)`**, never `SET LOCAL app.org_id = :org`: Postgres
    `SET`/`SET LOCAL` rejects bind parameters (the value must be a literal token), so the bound form
    will not parse. `set_config(..., is_local => true)` is the transaction-scoped function equivalent
    and takes a bound value. Being transaction-scoped, it never leaks `org_id` into the next pooled
    checkout (plain `set_config(..., false)` / plain `SET` would).
  - Repository classes (inside `services/<domain>/`) **always** apply `WHERE org_id = :org` on top of
    RLS — belt and suspenders, never either/or.
- **RLS — ENFORCED, UNCONDITIONALLY, SINCE F60 (migration `0015`)**. Every tenant table carries
  `ENABLE`/`FORCE ROW LEVEL SECURITY` + a `tenant_isolation` policy, live in every environment,
  never gated by a flag (`RLS_ENABLED` is vestigial — kept only because migration `0002` imports it):
  ```sql
  ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
  ALTER TABLE documents FORCE ROW LEVEL SECURITY;        -- applies even to table owner-ish roles
  CREATE POLICY tenant_isolation ON documents
    USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
    -- the NULLIF is load-bearing: a committed transaction-local GUC resets to '' (not NULL) on a
    -- pooled connection, and a bare ''::uuid cast raises instead of matching nothing.
  ```
  - `organizations` has no `org_id`; its policy keys on `id = NULLIF(current_setting('app.org_id', true), '')::uuid`.
  - **Role split (live since F60):** the app (API + arq worker) connects as the restricted **`app_user`**
    role (`NOLOGIN` in the migration — LOGIN/password provisioning is per-environment), genuinely
    subject to RLS; Alembic runs as the privileged table-owning **`migrator`** role (`BYPASSRLS`) via
    `MIGRATIONS_DATABASE_URL`. `tests/test_rls.py` connects as `app_user` specifically — a teeth-having
    isolation test, not just an app-level-filter assertion.
  - **Pre-tenant auth bootstrap** (signup/login, before any org is known): `auth_session(email)` sets a
    second transaction-local GUC (`app.auth_email`) and two permissive SELECT-only `auth_email_lookup`
    policies scope reads to exactly that email's user row and its org — nothing else.

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

## Hybrid search (BM25-style lexical + vector, RRF fusion) — shipped, `HYBRID_SEARCH_ENABLED`
- Native Postgres full-text search, no third-party extension: `chunks.content_tsv`, a `STORED`
  generated `tsvector` column (`to_tsvector('english', content)`) + GIN index (migration `0021`).
  **The ORM mapping needs `Computed("to_tsvector('english', content)", persisted=True)`** — without
  it, SQLAlchemy's `insertmanyvalues` batch-insert path sends an explicit `NULL` for the column on
  every insert, and Postgres rejects ANY explicit value (even NULL) into a `GENERATED ALWAYS` column,
  breaking ingestion entirely. `Computed()` here is DML-only signaling (excludes the column from
  INSERT/UPDATE); the actual DDL is owned by the migration's raw SQL.
- `ChunkRepository.search_chunks_lexical` uses `websearch_to_tsquery`/`ts_rank`, returns `ChunkHit`
  with `distance=None` (a lexical-only hit genuinely has no cosine distance — both `ChunkHit.distance`
  and `ContextBlock.distance` are `float | None`).
- Pure `fuse_rrf(vector_hits, lexical_hits, k=60)` (standard reciprocal-rank fusion) dedupes on
  `chunk_id` — the vector instance wins on collision since it carries a real distance. Composes with
  the reranker: effective candidate widening is
  `max(k, RERANK_CANDIDATE_K, HYBRID_CANDIDATE_K)` when both flags are on.

## arq (background workers)
- Task functions live in `app/services/<domain>/tasks.py` (currently only `app/services/ingestion/tasks.py`);
  `worker.py` only imports and registers them in `WorkerSettings.functions`. Enqueue from a service.
  Tasks are thin and call the domain's service functions. Pass the `request_id` and `org_id` in the job
  payload for tracing + scoping.
- **A worker opens its DB work via `tenant_session(org_id)` (from `app/config/db.py`) using the `org_id`
  from the job payload** — the same helper the request path uses — so background writes are tenant-scoped
  exactly like request writes (no HTTP `TenantContext` required).
- Ingestion stages are separate task functions chained on success; each is idempotent so retries
  are safe and a `FAILED` doc resumes from its last good stage.

## The 4 seams (our wrappers, in `app/services/seams/`)
- Real adapters wrap the vendor APIs; **fakes** are the default in tests/local:
  - `FakeEmbedder`: deterministic vector from `sha256(text)` → reproducible retrieval assertions.
  - `FakeLLM`: streams back a templated answer citing the provided context → tests citation mapping.
  - `FakeParser`: returns a fixed text + outline → tests structuring without a real PDF.
  - `FakeReranker`: identity passthrough stamping `rerank_score = 1.0 - distance` (or `0.0` when
    `distance is None`, e.g. a hybrid-search lexical-only hit) — deterministic, lets confidence-gate
    tests control the score via `FakeEmbedder` distance.
- Swap real⇄fake via per-seam config (`PARSER_MODE`, `EMBEDDER_MODE`, `LLM_MODE`, `RERANKER_MODE`);
  production wires real adapters, CI wires fakes. `RERANKER_ENABLED` (default `False`) is a SEPARATE
  switch from `RERANKER_MODE` — it gates whether the reranker is called at all; shipping the code
  changes nothing in existing behavior until explicitly turned on. Same one-flag-per-capability
  pattern for every V2-style addition since (`HYBRID_SEARCH_ENABLED`, `BROAD_QUERY_ENABLED`,
  `NOTEBOOK_OVERVIEW_ENABLED`, `CONTEXTUAL_EMBEDDING_ENABLED`, `ENRICHMENT_ENABLED`,
  `SEMANTIC_OUTLINE_ENABLED`, `HIERARCHICAL_RETRIEVAL_ENABLED`).
- **`RealReranker`** (`app/services/seams/real_reranker.py`): an `httpx` client (NOT an SDK wrapper
  like the OpenAI-compatible `RealEmbedder`/`RealLLM`) to a self-hosted BGE-reranker-v2-m3 served by
  Hugging Face Text-Embeddings-Inference (`docker-compose.yml`'s `reranker` service, port 8081,
  `RERANKER_URL`). POSTs `{"query", "texts", "raw_scores": false}` to `/rerank` — `raw_scores:false`
  means TEI returns sigmoid-normalized `[0,1]` scores (not raw logits), so `RERANK_MIN_SCORE`
  (the confidence-gate threshold) must be calibrated in `[0,1]`. Response is
  `[{"index": int, "score": float}, ...]`, NOT guaranteed sorted — `RealReranker.rerank` sorts
  explicitly. **On CPU-only hardware, TEI internally caps `max_batch_requests=4`** regardless of how
  many candidates are sent — the default `RERANK_CANDIDATE_K=25` can take long enough to exceed the
  client's HTTP timeout (60s); `retrieval/service.py`'s `_retrieve_hits` catches the resulting
  `SeamTransientError` and degrades to the unreranked candidates rather than failing the chat turn
  (confirmed 2026-07-30, live, against a real TEI instance — a first cold-start warmup alone took
  ~11.5 minutes on modest CPU hardware, so don't mistake a slow-but-alive container for a crash).

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
  2. **Object store:** `ObjectStore.delete` (added 2026-07-12) removes the blob(s) synchronously,
     after the DB commit — DB-first ordering means a failed blob delete only orphans a blob, never
     leaves a dangling row.
  3. **NOT built, still a known gap:** a periodic orphan-sweep job that would delete any blob whose
     `document_id` no longer exists in the DB (to self-heal a crash between DB commit and blob
     delete) — there is no "same DB tx" for blobs, and nothing sweeps orphans left by that gap today.

## Parser / OCR vendor
- **Resolved F23: OpenRouter's file-parser plugin**, called directly over HTTP from inside
  `RealParser` (`app/services/seams/real_parser.py`) — never hand-rolled OCR, never leaks
  outside the seam.
- Send the PDF as a base64 `file` content part on a `/chat/completions` call, with
  `plugins: [{"id": "file-parser", "pdf": {"engine": ...}}]`. The model/generated text is
  incidental (`max_tokens=1`) — only `choices[0].message.annotations[].file.content[]`
  (a list of `{type, text}` blocks) is read; concatenate the text blocks in order to build
  the canonical extracted text, and compute all char offsets against THAT text, never the
  source PDF bytes.
- **Engine routing**: try `cloudflare-ai` (free; `pdf-text` is deprecated and redirects here —
  use the current name) first; fall back to `mistral-ocr` (billed) only if the result is
  negligible (`PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE`, default 20 chars/page) — minimizes
  OCR spend, only pays for OCR on actually-scanned PDFs.
- **Heading structure**: both engines return markdown; parse `#`/`##`/`###` lines into the
  outline. Pass through flat (`outline=[]`) when a document has none — never fabricate
  structure. See architecture.md's "Real Parser vendor — resolved F23" for the accepted
  language/page-provenance limitations.
- Page count is read locally via `pypdf` (also where encrypted PDFs are detected), never
  trusted from the API response.
- DOCX is out of scope — a future adapter branch, not built.

## When unsure about a library's current API
- Check the seam/wrapper first (we may already encapsulate it). Then web-search the official docs for
  the version in `pyproject.toml`. Prefer our wrapper's pattern over a generic snippet.
