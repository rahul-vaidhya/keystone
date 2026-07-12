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

## Folder map (single-MVC — Express-style backend + React SPA; 2026-07-02 refactor)

**2026-07-02 UPDATE:** The codebase moved from layer-first MVC on both tiers to a single MVC: backend is Express-style (routes + controllers + services, no view layer) and frontend is conventional React SPA (pages, layouts, components). See the refactor mapping in memory.md "Single-MVC refactor (2026-07-02)".

**Backend is Express-style MVC (single MVC for the whole system)**: one package per layer (`models/`, `routes/`, `controllers/`, `services/`, `middleware/`, `config/`, `utils/`), one file per domain (auth, documents, ingestion, knowledge, retrieval, chat) inside each layer. models = ORM + API schemas; routes = HTTP wiring; controllers = thin handlers; services = business logic + SQL repository classes. The frontend SPA is the view layer — there is deliberately no second MVC inside it.

```
backend/
  app/
    config/             # settings.py (pydantic-settings), db.py, logging.py
    middleware/         # context.py (TenantContext), deps.py (current_user/get_ctx)
    utils/              # tokens.py, passwords.py, constants.py, http.py (utilities)
    services/           # business logic + SQL repositories, one file OR subpackage per domain:
                        #   auth.py, documents/ (subpackage), ingestion/ (subpackage),
                        #   knowledge.py, retrieval.py, chat.py, base.py (BaseRepository),
                        #   seams/ (3 seam Protocols + fakes + real adapters),
                        #   storage.py, queue.py
    models/             # ORM models + Pydantic API schemas (merged), one file per domain:
                        #   auth.py, documents.py, ingestion.py, knowledge.py, retrieval.py,
                        #   chat.py  (retrieval has schemas only, owns no table)
    routes/             # HTTP path wiring only, one file per domain: auth.py, documents.py,
                        #   ingestion.py, notebooks.py (= knowledge domain), retrieval.py, chat.py
    controllers/        # thin handlers (parse/validate/call service/shape response),
                        #   one file per domain: auth.py, documents.py, ingestion.py,
                        #   notebooks.py, retrieval.py, chat.py
  tests/
  migrations/           # alembic
  worker.py             # arq entrypoint
  main.py               # FastAPI entrypoint
frontend/
  src/
    pages/              # routed screens: HomePage, LoginPage, SignupPage, DocumentsPage, etc.
    layouts/            # AppShell, Sidebar (shared page containers)
    components/         # reusable non-routed UI: StatusBadge, FolderTree, ChatPanel, etc.
    services/           # API client namespaces: authApi, documentsApi, chatApi, etc. +
                        #   http.ts (apiFetch wrapper), AuthContext.tsx, useAuth.ts hook
    types/              # TypeScript types extracted from API schemas
    styles/             # CSS (index.css)
  index.html
context/                # these files
```

**Single-MVC terminology:** "domain" names match routing (auth, documents, knowledge → notebooks) and services (same names).
The **routes/ layer** is wiring only (FastAPI `APIRouter` + `@router.post` path definitions).
The **controllers/ layer** is thin handlers (request validation → service call → response shape).
The **services/ layer** holds business logic AND repository classes (SQL). Services reach other domains only via their service module; never via repository classes or models directly.

**Domain-naming note:**
The "identity" domain (orgs/users/auth) is named `auth` in `routes/auth.py`, `controllers/auth.py`, `services/auth.py`, `models/auth.py`. The "knowledge" domain (notebooks) is named `notebooks` only in `routes/notebooks.py` and `controllers/notebooks.py` (matches the public "Notebook" API terminology); its internal service and models stay `knowledge_base*` in `services/knowledge.py` and `models/knowledge.py` — locked since F30, unchanged by the single-MVC refactor.

Each domain's logic lives in one file or subpackage per layer:
`app/routes/<domain>.py` (HTTP path wiring) · `app/controllers/<domain>.py` (handlers) · `app/services/<domain>.py` or `services/<domain>/` (business logic + repository classes) · `app/models/<domain>.py` (ORM + Pydantic schemas, merged).

### Package-layout convention (locked, applies to all backend code)

Each domain's service/model file starts flat. A flat file is **promoted to a subpackage of the same
name** (e.g. `services/documents.py` → `services/documents/__init__.py` + submodules) only
when **both** hold:

1. It exceeds roughly **200 lines**, **and**
2. It contains **2+ genuinely independent responsibility groups** — different tables,
   different pipeline stages, different vendor adapters — not just "many small methods on
   one cohesive concern."

A file that's long but is one class/one concern with many small methods is **not**
promoted. A file with multiple small classes but no real logic (e.g. several plain ORM
declarations in one `models/<domain>.py`) is **not** promoted either — declaring data
classes together is normal, not drift. **Do not pad**: a domain that legitimately owns no
table keeps no `models/retrieval.py` file at all (though it has `models/retrieval.py` for
schemas/types only; `retrieval` is correct as-is — models/schemas-only, `services/retrieval.py`
only, no repository class). Over-splitting to satisfy "one file per domain, always" is
itself a violation of this convention, not a stricter reading of it.

When `services/<domain>.py` is split, the original public surface (the module-level class/singleton/function names other modules and tests import) **must resolve at the exact same import path afterward**, via the subpackage's `__init__.py` re-exporting everything a real call site uses today — including any private (underscore-prefixed) names a test file imports directly (debug-only scripts are not load-bearing the same way: fix their one-line import instead of promoting a deliberately-private helper to the package's public API). Composition inside a split `services/<domain>/` is **always delegation to free functions taking explicit arguments** (the same shape `services/ingestion/` and `services/documents/` use) — **never mixins**. If a clean free-function split would need many self-like positional args threaded through every call, that's evidence the responsibilities aren't actually separable; leave the file flat rather than force a bad split.

**The ORM registration rule:** every ORM model class must still be imported, by name, at
metadata-assembly time (today: `migrations/env.py`'s side-effect imports of each domain's
`models/<domain>.py`). If a `models/<domain>.py` is ever promoted to a `models/<domain>/`
subpackage, its `__init__.py` **must import every model class** (not just re-export the
ones other modules happen to use) — Alembic only sees a table if its class has been
imported somewhere on the path to `Base.metadata`; a model class that's merely defined in
an unimported submodule silently vanishes from autogenerate/migrations with no error. Note:
since the 2026-07-02 single-MVC refactor merged schemas into models, `models/<domain>.py`
now holds both ORM classes and Pydantic schemas; the rule still applies — ensure every
ORM class in the module is imported at metadata-assembly time. No `models/<domain>.py` has
been promoted yet (none crossed the 200-line/independent-responsibility threshold as of F52)
— this rule is recorded now, before it's needed, so the first domain that does cross it
doesn't relearn this the hard way.

**Reference domain:** `knowledge` and `retrieval` — every domain's service/model file is one
cohesive concern at a sane size, and `retrieval` correctly has no repository class since
it owns no table. Hold new domains to this, not to "more files is more structured."

This convention was initially applied in F00–F31 to split four files that exceeded 200
lines: `services/documents/`, `services/ingestion/`, `services/documents/`, and
`services/seams/` (the latter holding 3 vendor adapters + fakes). The **2026-07-02
single-MVC refactor** then restructured the entire codebase from the 2026-07-01
layer-first MVC layout to the current Express-style single-MVC layout, with zero
logic/behavior/schema/API change — the subpackages that had already been split kept their
split shape, just in new locations (`services/documents/`, `services/ingestion/`,
`services/seams/`). This convention applies to every feature from F40 onward under the
current single-MVC paths.

## Boundaries (HARD RULES)
1. A domain's service calls another domain **only through its `services/<other>.py`** — never directly
   to repository classes or ORM models. Example: `app/services/chat.py` asks `app/services/retrieval.py`
   for context; it never imports repository classes from `services/ingestion.py` (which owns `chunks`/
   `embeddings`) directly. Within a domain, repository classes live inside the `services/<domain>/`
   module (they are implementation details, accessed only by that domain's service layer).
2. **No SQL outside the repository classes inside `services/<domain>.py` (or `services/<domain>/repository.py` if split).**
   **No business logic in `routes/<domain>.py` or `controllers/<domain>.py`.** Routes wire paths; controllers
   validate requests and call services; services hold logic; repositories hold SQL.
3. **Every query is scoped by `org_id`.** A `TenantContext` carries it; the base repository (`BaseRepository` in
   `app/services/base.py`) applies the app-level `WHERE org_id = :org` filter on **every** query, **always**
   (independent of any flag). Postgres **RLS** is the backstop — but it is **deferred to Phase 6 (Security Hardening),
   gated by `RLS_ENABLED` (default OFF in dev/test)**. The schema, policies, and role split are designed now;
   the teeth are switched on before real customer data. App-level scoping is the MVP guarantee.
4. External services are reached **only through a seam** (`Parser`, `Embedder`, `LLM`). Tests use fakes.
   Seams live in `app/services/seams/` (protocols, fakes, and real adapters).

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

**Seam mode is PER-SEAM, not one global switch (decided F23):** the original single
`SEAMS_MODE=fake|real` flag is refined into three independent switches — parser, embedder, and
llm each resolve `fake`/`real` on their own (e.g. `PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE`).
This lets the real parser be validated against real documents while the embedder and LLM stay
on fakes (no API cost/keys needed for that validation). **Default for every seam, in every
environment, remains `fake`** — real is opt-in per seam, never the app default, and the
offline Testcontainers CI suite always runs fully fake regardless of what's configured locally.
The 3-seam rule itself (Parser/Embedder/LLM, nothing else) is unchanged — this only changes how
each seam's mode is selected, not how many seams exist or their Protocol shapes.

**Real Parser vendor — resolved F23 (OpenRouter file-parser plugin):**
- `RealParser` calls OpenRouter's `/chat/completions` file-parser plugin directly over HTTP
  (lazy-imported `httpx`) — a **separate adapter/vendor call** from `RealEmbedder`/`RealLLM`,
  even though both happen to be OpenRouter-compatible endpoints. Two seams, two adapters, never
  collapsed into one client.
- **Engine routing** (minimizes OCR cost): try the free `cloudflare-ai` text engine first
  (the current name — `pdf-text` is **deprecated and redirects to `cloudflare-ai`**, confirmed
  against OpenRouter's docs during F23; target the current name directly). If the result is
  negligible (< `PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE`, default 20, chars per page), retry
  once with billed `mistral-ocr`. If both are negligible, raise — never persist garbage.
- **PDF only.** Non-PDF mime is rejected with a `ValueError`. DOCX is a deliberately deferred
  future adapter branch, not built in F23.
- **Heading structure is recovered from the provider's markdown output, never fabricated.**
  Both engines return markdown; `#`/`##`/`###` lines are parsed into the `OutlineNode` tree
  (`_parse_markdown_outline` in `seams/real_parser.py`), with each heading's range extended to the next
  heading at the same-or-shallower level (not just the next heading in the flat list), so a
  parent's range still covers its children. If a document's output has no markdown headings,
  the outline is `[]` and F21's degenerate-outline contract (one root section) takes over —
  this is a valid, expected per-document finding, not a bug. Heading parsing logic lives in
  `app/services/seams/real_parser.py`.
- **Two known, accepted F23 findings, not bugs to fix:**
  1. `language` is hardcoded `"en"` — the provider doesn't return detected language. A future
     language-detection pass (if ever needed) is a separate, additive concern.
  2. Page-level provenance does NOT survive parsing — markdown has no page-boundary markers, so
     every recovered heading gets `page_start=1, page_end=page_count` (document-level, not a
     real per-heading span). The F23 integration harness prints whether this held per document.
- **Idempotent retry without re-paying OCR**, realized at the **ingestion-stage level**, not by
  threading OpenRouter's own annotation/hash-reuse objects through the generic `ParsedDoc` seam
  type (that mechanism is for multi-turn chat reuse and doesn't fit a stateless one-shot
  `extract()` call, and would have broken the "same shape for fake and real" seam contract).
  Instead, `run_parsing_stage` checks whether the parsing artifact already exists in the object
  store before calling `parser.extract` — if a prior attempt crashed after persisting the
  artifact but before the status write, the retry reuses it and skips the parser call entirely.
  This benefits the fake parser too (closed a latent gap, not F23-only).
- PDF page count is read **locally** via lazy-imported `pypdf`, never trusted from the API
  response — this is also where an encrypted PDF is detected and rejected, before any network
  call is made.

---

## Ingestion pipeline (staged, idempotent, resumable)

**Authoritative status enum** (`DocumentStatus`) lives in `app/models/documents.py` and is referenced
everywhere (controller responses, repository writes, worker transitions, UI color mapping).
There is exactly one source of truth — do not invent ad-hoc states (there is no `parsed` state):
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
    allowed = resolve_allowed_documents(req.ctx)   # owner/admin: all org docs. Others: gated by
                                                   # Access Roles' granted tags (2026-07-12) —
                                                   # see docs/access-roles-dnd-plan.md.
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
  folder_id uuid null fk,                 -- [now] one home folder (a permission boundary ONLY
                                           -- when the folder carries a tag granted to an Access
                                           -- Role — otherwise open to the whole org, unchanged)
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
| Permissions | Access Roles: tag-granted resource access (2026-07-12) | per-user grants, connectors, browsing-endpoint gating (V2) |
| Folders/tags | full nav tree + tags | connector-sourced folders |
| Sections | tree + structural fields | summary/topics (V2 enrichment) |
| Embeddings | `owner_type='chunk'` | section/document rows (V2, insert-only) |
| KG tables | NOT migrated; shape decided | created + extraction (V3) |
| Retrieval | flat vector | hierarchical (V2) + graph (V3) behind flags |
| Reranker / eval framework | none (manual golden questions) | added only when quality work demands |
