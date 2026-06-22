# architecture.md — Technical Foundation (HEAVYWEIGHT — read fully before backend work)

## Stack
- **Backend:** Python, FastAPI (HTTP), arq workers (background jobs).
- **DB:** Postgres + pgvector (one database — metadata, chunks, vectors all here).
- **Object store:** S3-compatible (raw files + persisted ingestion artifacts).
- **Queue:** Redis (arq).
- **Frontend:** Vite + React SPA, TanStack Query, SSE for streaming chat. Presentational only.
- **External (behind seams):** document parser/OCR API, embeddings API, LLM API.

## Guiding principle (the spine of the whole design)
**Structural metadata is captured NOW (free — it falls out of parsing). Semantic enrichment
is designed-for now but populated LATER (expensive — it needs LLM calls).**
Because the structural skeleton + provenance are persisted, V2 (hierarchical) and V3 (graph)
run as **backfill jobs over already-ingested data** — never a re-parse, never a schema rewrite.

---

## Folder map (modular monolith)

```
backend/
  app/
    platform/        # config, db session, TenantContext, logging, the 3 seam Protocols + fakes
    identity/        # orgs, users, auth
    documents/       # folders, tags, documents, upload, dedupe, object storage
    ingestion/       # staged pipeline + arq tasks
    knowledge/       # notebooks (the reference join)
    retrieval/       # query embed, search, context assembly (strategy behind flags)
    chat/            # conversations, grounded generation, citation mapping, debug bundle
  tests/
  migrations/        # alembic
  worker.py          # arq entrypoint
  main.py            # FastAPI entrypoint
frontend/
  src/{features,components,lib(api client),...}
context/             # these files
```

Each backend module has the SAME shape:
`router.py` (HTTP only) · `service.py` (use cases / logic) · `repository.py` (ALL SQL) ·
`schemas.py` (Pydantic + domain types) · `tasks.py` (its background work).

## Boundaries (HARD RULES)
1. A module calls another module **only through its `service`** — never its repository or tables.
   Example: `chat` asks `retrieval.service` for context; it never touches `chunks` directly.
2. **No SQL outside `repository.py`.** **No business logic in `router.py`.** Routers validate + call services.
3. **Every query is scoped by `org_id`.** A `TenantContext` carries it; the base repository applies
   the app-level `WHERE org_id = :org` filter on **every** query, **always** (independent of any flag).
   Postgres **RLS** is the backstop — but it is **deferred to Phase 6 (Security Hardening), gated by
   `RLS_ENABLED` (default OFF in dev/test)**. The schema, policies, and role split are designed now;
   the teeth are switched on before real customer data. App-level scoping is the MVP guarantee.
4. External services are reached **only through a seam** (`Parser`, `Embedder`, `LLM`). Tests use fakes.

### Tenancy plumbing (built NOW; enforcement gated)
- **Every tenant-scoped table carries `org_id`** — including join tables (`document_tags`,
  `knowledge_base_documents`) and child tables (`messages`, `message_traces`). No "scope via parent
  join" exceptions; the base repository can always filter directly.
- **One transaction-scoped, HTTP-agnostic session helper, used by BOTH requests AND workers:**
  ```python
  @asynccontextmanager
  async def tenant_session(org_id):
      async with sessionmaker() as s, s.begin():
          if settings.RLS_ENABLED:                       # OFF in MVP dev/test
              await s.execute(
                  text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
              )
          yield s
  ```
  - Use `set_config('app.org_id', :org, true)`, **not** `SET LOCAL app.org_id = :org` — Postgres
    `SET`/`SET LOCAL` does not accept bind parameters (it takes a literal token), so the parameterised
    form fails to parse. `set_config(..., is_local => true)` is the function equivalent of `SET LOCAL`
    and accepts a bound value safely.
  - The `is_local => true` (third arg) → the GUC resets at transaction end, so a pooled connection
    cannot leak one request's `org_id` into the next.
  - The **arq worker calls the SAME helper** with `org_id` from the job payload — closing the
    background-job tenancy gap (workers have no HTTP request, but they have this helper).
- **Repositories ALWAYS apply `WHERE org_id = :org`** regardless of `RLS_ENABLED` (functional
  correctness, near-zero friction).
- **Designed now, enabled in Phase 6 (do NOT turn on yet):** the RLS policies, `FORCE ROW LEVEL
  SECURITY`, and the DB-role split — a restricted non-owner **`app_user`** role (subject to RLS) vs a
  privileged **`migrator`** role (runs Alembic, owns tables). Migrations/Testcontainers run as
  `migrator` (RLS bypassed) today; Phase 6 switches the app to connect as `app_user`.
- **RLS predicate notes (for the Phase 6 migration):**
  - `organizations` keys on `id = current_setting('app.org_id', true)::uuid` (it has no `org_id`).
  - `users` and all other tenant tables use the normal `org_id = current_setting('app.org_id', true)::uuid`.
  - The `true` (missing_ok) form means an **unset GUC → NULL → zero rows** (fail-closed).

## The 3 seams (and only these)
```python
class Parser(Protocol):
    async def extract(self, blob: bytes, mime: str) -> ParsedDoc: ...   # text + outline + pages + language
class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
class LLM(Protocol):
    async def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...   # async-IO rule
```
Fakes: hash-based deterministic embedder; echo-context LLM; fixed-output parser. → run the whole
app and test suite with no API keys, no cost, reproducible. pgvector, object store, and the queue
are called directly (we are not swapping Postgres). A `Reranker` seam is added in V2, not now.

---

## Ingestion pipeline (staged, idempotent, resumable)

**Authoritative status enum** lives in `documents/status.py` and is referenced everywhere
(router responses, repository writes, worker transitions, UI color mapping). There is exactly
one source of truth — do not invent ad-hoc states (there is no `parsed` state):
```
UPLOADED → PARSING → STRUCTURING → EMBEDDING → READY        (+ FAILED, with failed_stage)
```
- `UPLOADED` = stored in object store + queued. `PARSING`/`STRUCTURING`/`EMBEDDING` = that stage
  in progress. `READY` = queryable. `FAILED` = stage threw; `failed_stage` + `error_detail` set.

```
CORE PATH (MVP — must reach READY to be queryable)
  UPLOADED → PARSING → STRUCTURING → EMBEDDING → READY
                                          └─ any stage → FAILED (failed_stage, error_detail)

POSTPONED, BACKFILLABLE JOBS (designed now, OFF in MVP, run over existing corpus later)
  enrichment  [V2, flag ENRICHMENT_ENABLED]      sections/docs → LLM summary+topics → embed as section/document
  extraction  [V3, flag GRAPH_EXTRACTION_ENABLED] chunks → entities + relationships (with provenance)
```

- **parsing:** parser returns text + outline + page map + **language** (the `Parser` seam detects
  language and returns it on `ParsedDoc`). Persist raw text + structure artifact to object store.
  Set `documents.language`, `page_count`.
- **structuring:** build `sections` tree from the outline (STRUCTURAL fields only; summary/topics null);
  create `chunks` linked to `section_id`, with `char_start/char_end`. Persist. *This stage makes
  hierarchy free later.*
  - **Degenerate-outline contract:** if there is no usable outline (plain TXT, headingless PDF),
    create ONE root section spanning the full char range (`char_start=0`, `char_end=len`) and attach
    every chunk to it. The invariants always hold: **every chunk has a `section_id`; every section
    has offsets.** No chunk is ever orphaned from the tree.
- **embedding:** embed chunks; upsert `embeddings(owner_type='chunk')`.

Idempotency: `chunk_id = hash(document_id, ordinal, content)` + unique constraints ⇒ re-running a
stage upserts (no duplicates); a failed doc resumes from its last good stage.

## Retrieval pipeline (one function, strategy behind flags)
```python
async def retrieve(req) -> Context:
    allowed = resolve_allowed_documents(req.ctx)   # MVP: returns all org docs. The single seam
                                                   # where V2 groups/grants permission logic slots in.
    scope   = req.notebook.document_ids & allowed
    qvec    = await embedder.embed([req.query])
    if   FLAGS.graph_retrieval: hits = graph_then_vector(req, qvec, scope)   # V3
    elif FLAGS.hierarchical:    hits = coarse_to_fine(req, qvec, scope)      # V2
    else:                       hits = flat_vector(qvec, scope, k=30)        # MVP
    return assemble_context(hits[:8])              # numbered, with source refs for citations
```

---

## DATA MODEL — what exists today, marked [now] / [later]

`[now]` = populated in MVP ingestion. `[later]` = column exists now, filled by V2/V3 backfill.
`(create in V3)` = table designed here, migrated only in V3 (depends only on provenance stored now).

```sql
-- tenancy & navigation
organizations(id uuid pk, name, plan, created_at)
users(id uuid pk, org_id fk, email citext,
      role text,                              -- [now] enum: 'owner' | 'admin' | 'member'
                                              --   org creator → owner; invites default → member;
                                              --   debug bundle gates on role in ('owner','admin')
      created_at)

folders(id uuid pk, org_id fk, parent_id uuid null fk→folders.id,
        name text, path text,            -- [now] materialized path 'HR/Policies'
        created_at)
tags(id uuid pk, org_id fk, name text)
document_tags(org_id fk, document_id fk, tag_id fk, primary key(document_id, tag_id))
            -- [now] org_id on the join table too: every tenant-scoped table carries org_id,
            -- no "scope via parent join" exception

-- documents (document-level metadata)
documents(
  id uuid pk, org_id fk,
  folder_id uuid null fk,                 -- [now] one home folder (NOT a permission boundary)
  title text,                             -- [now]
  source_type text,                       -- [now] upload | gdrive | url ...
  storage_key text, mime_type text, byte_size bigint,
  checksum text,                          -- [now] dedupe; unique(org_id, checksum)
  page_count int, language text,          -- [now] set at parse
  status text, failed_stage text null, error_detail text null,  -- [now] pipeline state
  doc_summary text null,                  -- [later] V2 enrichment
  doc_topics jsonb null,                  -- [later] V2 enrichment
  metadata jsonb default '{}',            -- [now] extensible (author, dates, connector ids) w/o migration
  created_by fk, created_at, updated_at,
  unique(org_id, checksum))

-- sections (the hierarchical backbone — THE key future-proofing)
sections(
  id uuid pk, org_id fk, document_id fk on delete cascade,   -- [now] dies with the document
  parent_section_id uuid null fk→sections.id on delete cascade,   -- [now] self-ref hierarchy
  ordinal int, depth int,                       -- [now] sibling order, heading level
  path text,                                    -- [now] '1.2.3' fast subtree queries
  heading text,                                 -- [now]
  page_start int, page_end int,                 -- [now]
  char_start int, char_end int,                 -- [now] offsets → provenance
  summary text null, topics jsonb null,         -- [later] V2 enrichment
  created_at)

-- chunks (the retrievable units)
chunks(
  id uuid pk,                             -- deterministic hash(document_id, ordinal, content)
  org_id fk, document_id fk on delete cascade,   -- [now] dies with the document
  section_id uuid null fk→sections.id on delete cascade,    -- [now] links chunk into tree (unused by MVP retrieval)
  ordinal int, content text, token_count int,   -- [now]
  char_start int, char_end int,           -- [now] provenance for citations + future KG
  metadata jsonb default '{}',            -- [now] cached heading breadcrumb for citation display
  created_at)

-- embeddings (polymorphic, multi-granularity index)
embeddings(
  id uuid pk, org_id fk,
  document_id uuid fk on delete cascade,  -- [now] denormalized → scope filter needs no join; dies with the document
  owner_type text,                        -- [now] 'chunk' | [later] 'section','document'
  owner_id uuid,
  model text, dim int,                    -- [now] provenance: which model produced this vector, at what dim
  embedding vector(1536), created_at,
  unique(owner_type, owner_id, model))    -- idempotent re-embed
-- indexes: HNSW on embedding; btree(org_id, document_id, owner_type)
-- MVP runs ONE active model. The model/dim columns are provenance; retrieval filters
-- `model = :active_model` (so a re-embed never returns duplicate hits per chunk).
-- ASTERISK (the one exception to the additive V2/V3 promise):
--   • SAME-dimension model swap → free (still vector(1536), new rows under the new model name).
--   • DIFFERENT-dimension model → needs a MIGRATION (a separate vector(N) column or table per dim;
--     a single fixed-width vector column cannot hold mixed dimensions).

-- knowledge bases (notebooks = references, not copies)
knowledge_bases(id uuid pk, org_id fk, name, description, created_by, created_at)
knowledge_base_documents(org_id fk,                          -- [now] org_id on the join table too
                         knowledge_base_id fk, document_id fk on delete cascade, added_at,
                         primary key(knowledge_base_id, document_id))

-- chat
conversations(id uuid pk, org_id fk, knowledge_base_id fk, user_id fk, created_at)
messages(id uuid pk, org_id fk,           -- [now] org_id on the child table too (no scope-via-parent)
         conversation_id fk on delete cascade,
         role text, content text,
         citations jsonb,                 -- [{chunk_id, document_id, char_start, char_end, score}]
         created_at)

-- answer trace = the F42 admin debug bundle, PERSISTED (not recomputed)
message_traces(
  id uuid pk, org_id fk,                  -- [now] admin-read-only (role in 'owner','admin')
  message_id fk on delete cascade,        -- dies with its message (which dies with its conversation)
  hits jsonb,                             -- retrieved chunks + scores
  final_prompt text,                      -- the exact assembled prompt sent to the LLM
  raw_output text,                        -- the model's raw response
  created_at)

-- knowledge graph (DESIGN NOW, CREATE IN V3 — needs only provenance already stored)
entities(id uuid pk, org_id fk, name, normalized_name, type, metadata jsonb)
mentions(id uuid pk, org_id fk, entity_id fk, document_id fk, section_id fk null, chunk_id fk,
         char_start int, char_end int)
relationships(id uuid pk, org_id fk, subject_entity_id fk, predicate text,
              object_entity_id fk, chunk_id fk, confidence real)
```

### How this one schema serves all four retrieval futures
- **Document/flat retrieval (MVP):** search `embeddings WHERE owner_type='chunk'`, filter
  `document_id IN (notebook ∩ allowed)`.
- **Section retrieval (V2):** chunks carry `section_id` + offsets → expand a hit to its section or
  parent via the `sections` tree. No re-ingest.
- **Hierarchical/indexed (V2):** enrichment fills `*.summary` + inserts `embeddings(owner_type=
  'section'|'document')` into the SAME table. Coarse-to-fine routing needs no schema change.
- **Knowledge graph (V3):** extraction reads existing chunks → fills `entities/mentions/relationships`,
  each triple pointing back to `chunk_id` + offsets. Possible only because provenance was stored in MVP.

---

## MVP / V2 / V3 (strictly additive)
- **MVP — flat RAG, NotebookLM-style.** Core ingestion path + flat vector retrieval + grounded
  cited chat + tenant isolation. The whole shippable product.
- **V2 — hierarchical / indexed.** Turn on `enrichment` backfill; add `coarse_to_fine` +
  parent-expansion strategies behind flags; optionally add the `Reranker` seam. New tables: none.
- **V3 — knowledge graph / advanced.** Create `entities/mentions/relationships`; run `extraction`
  backfill; add `graph_then_vector`; layer hybrid (vector+BM25) and any agentic multi-hop here.

## Build-now vs designed-for-postponed
| Concern | Build now | Postponed (designed-for) |
|---|---|---|
| Tenancy (app-level) | `org_id` on every table + always-on app filter + `tenant_session` plumbing | — |
| Tenancy (enforced RLS) | policies + role split WRITTEN, flag OFF | **Phase 6:** `RLS_ENABLED` on, `app_user`/`migrator` split, `FORCE RLS` |
| Tenancy (isolation model) | shared DB, `org_id` rows | per-tenant DB/schema (Enterprise) |
| Permissions | none; `allowed_document_ids` param = all org docs | groups/grants → compute the set (V2) |
| Folders/tags | full nav tree + tags | connector-sourced folders |
| Sections | tree + structural fields | summary/topics (V2 enrichment) |
| Embeddings | `owner_type='chunk'` | section/document rows (V2, insert-only) |
| KG tables | NOT migrated; shape decided | created + extraction (V3) |
| Retrieval | flat vector | hierarchical (V2) + graph (V3) behind flags |
| Reranker / eval framework | none (manual golden questions) | added only when quality work demands |
