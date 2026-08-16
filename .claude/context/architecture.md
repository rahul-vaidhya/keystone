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
                        #   knowledge/ (subpackage), retrieval/ (subpackage), chat/ (subpackage),
                        #   access_roles.py, embed.py, evals.py, queue.py, storage.py,
                        #   base.py (BaseRepository),
                        #   seams/ (4 seam Protocols — Parser/Embedder/LLM/Reranker — + fakes
                        #   + real adapters)
    models/             # ORM models + Pydantic API schemas (merged), one file per domain:
                        #   auth.py, documents.py, ingestion.py, knowledge.py, retrieval.py,
                        #   chat.py, access_roles.py, embed.py, evals.py
                        #   (retrieval has schemas only, owns no table)
    routes/             # HTTP path wiring only, one file per domain: auth.py, documents.py,
                        #   ingestion.py, notebooks.py (= knowledge domain), retrieval.py, chat.py,
                        #   access_roles.py, embed.py, evals.py
    controllers/        # thin handlers (parse/validate/call service/shape response),
                        #   one file per domain: auth.py, documents.py, ingestion.py,
                        #   notebooks.py, retrieval.py, chat.py, access_roles.py, embed.py, evals.py
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
   `app/services/base.py`) applies the app-level `WHERE org_id = :org` filter on **every** query, **always**.
   Postgres **RLS** is the always-on backstop since F60 (migration `0015`): policies + `FORCE ROW LEVEL
   SECURITY` on every tenant table, unconditionally — enforcement does not depend on any flag (`RLS_ENABLED`
   is vestigial; it survives as a settings field only because migration 0002 imports it at runtime).
   App-level scoping is the first line; RLS holds even when that filter is forgotten.
4. External services are reached **only through a seam** (`Parser`, `Embedder`, `LLM`). Tests use fakes.
   Seams live in `app/services/seams/` (protocols, fakes, and real adapters).

### Tenancy plumbing (ENFORCED since F60 — migration `0015`)
- **Every tenant-scoped table carries `org_id`** — including join tables (`document_tags`,
  `knowledge_base_documents`) and child tables (`messages`, `message_traces`). No "scope via parent
  join" exceptions; the base repository can always filter directly.
- **One transaction-scoped, HTTP-agnostic session helper, used by BOTH requests AND workers —
  and since F60 it is the ONLY sanctioned way to open a session** (a guard test in
  `tests/test_rls.py` fails the build on any bare `sessionmaker()` outside `config/db.py`):
  ```python
  @asynccontextmanager
  async def tenant_session(org_id):
      async with sessionmaker() as s, s.begin():
          await set_org_guc(s, org_id)      # ALWAYS — enforcement never depends on a flag
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
- **Repositories ALWAYS apply `WHERE org_id = :org`** on top of RLS (belt and suspenders).
- **The DB-role split (live since F60):** the app (API + arq worker) connects as the restricted
  **`app_user`** role (subject to RLS, `NOLOGIN` in the migration — LOGIN/password provisioning is
  per-environment); Alembic runs as the privileged table-owning role via `MIGRATIONS_DATABASE_URL`
  (falls back to `DATABASE_URL`; dev/test use one superuser URL for both — superusers bypass RLS
  even under FORCE, which is why the ordinary test suite is unaffected). `migrator` has `BYPASSRLS`
  so future data-backfill migrations aren't blocked by FORCE RLS.
- **RLS predicate notes (as shipped in migration `0015`):**
  - `organizations` keys on `id`; `users` and all other tenant tables key on `org_id`.
  - The predicate is `NULLIF(current_setting('app.org_id', true), '')::uuid` — the NULLIF is
    load-bearing: once any transaction on a pooled connection has set_config'd the GUC, it resets
    to `''` (empty string, NOT missing) at transaction end, and a bare `''::uuid` cast RAISES
    instead of matching nothing. NULLIF turns both "never set" (NULL) and "reset" (`''`) into
    NULL → **zero rows (fail-closed)**.
  - **Pre-tenant auth bootstrap:** signup/login can't run under an org GUC (no org known yet), so
    `auth_session(email)` (`config/db.py`) sets a second transaction-local GUC, `app.auth_email`,
    and two permissive SELECT-only policies (`auth_email_lookup` on `users` and `organizations`)
    widen reads to exactly the named email's user rows and their orgs — nothing else. Login
    switches INTO the matched org's scope mid-transaction via `set_org_guc` before its
    lockout-counter writes; signup pre-generates the new org's id client-side and sets the GUC
    before the org+owner INSERTs (so `WITH CHECK` passes).
  - **Every future tenant-scoped table must ship its own `ENABLE`/`FORCE` + `tenant_isolation`
    policy + `app_user` grant in its own migration** — 0015 only covers the 18 tables that existed
    at F60.

## The 4 seams (and only these)
```python
class Parser(Protocol):
    async def extract(self, blob: bytes, mime: str) -> ParsedDoc: ...   # text + outline + pages + language
class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
class LLM(Protocol):
    async def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...   # async-IO rule
class Reranker(Protocol):
    async def rerank(self, query: str, candidates: list[ChunkHit], top_k: int) -> list[ChunkHit]: ...
```
Fakes: hash-based deterministic embedder; echo-context LLM; fixed-output parser; identity-passthrough
reranker. → run the whole app and test suite with no API keys, no cost, reproducible. pgvector,
object store, and the queue are called directly (we are not swapping Postgres).

**`Reranker` shipped 2026-07-28** (the V2 seam originally scoped as "added only when quality work
demands it" — that trigger fired). `RERANKER_ENABLED` (default `False`) is a separate switch from
`RERANKER_MODE` — gates whether `RetrievalService._retrieve_hits` widens the candidate kNN pool
(`candidate_k = max(k, RERANK_CANDIDATE_K)`) and reranks it back down
(`final_k = min(k, RERANK_TOP_K)`); `RERANKER_MODE` (fake|real) only matters once enabled. Real impl (current, dev)
= self-hosted BGE-reranker-v2-m3 via Hugging Face TEI (`docker-compose.yml`'s `reranker` service) —
see librarydocs.md "The 4 seams" for the HTTP contract, timeout/fallback behavior, and the CPU-only
warmup-time gotcha (live-verified 2026-07-30).

**Locked decision (2026-08-04): production deployment will swap self-hosted TEI for a hosted
cross-encoder-as-a-service API** (e.g. Cohere Rerank, Jina Reranker, Voyage rerank-2) — same
cross-encoder architecture, someone else's GPU. Reason: BGE-reranker-v2-m3's CPU-only warmup
(~11.5 min, confirmed 2026-07-30) and per-query inference cost are real operational weight for
this team's infra; a hosted rerank API removes that entirely for a small per-call cost. This is a
**pure adapter swap, not a design change**: write a new `real_reranker_<vendor>.py` implementing
the exact same `Reranker` Protocol (`rerank(query, candidates, top_k) -> list[ChunkHit]`), select
it the same way `RERANKER_MODE` already does. `RetrievalService._retrieve_hits`'s widen/rerank/
fallback logic is unchanged — it only calls the `Reranker` Protocol, never TEI specifically. Local
dev can keep `RERANKER_MODE=fake|real` (self-hosted TEI) — this decision is about what production
uses, not about ripping out local/offline dev capability. **Trade-off to weigh at build time**:
this sends chunk text to a third-party API — a real tension for a product whose pitch is "your
documents never leave your infrastructure" (see librarydocs.md's identical caution about SaaS
tracing vendors). LLM-as-reranker was considered and rejected again for the same reasons as the
original research below (cost/latency, and poor score calibration for the confidence gate's fixed
threshold) — a hosted cross-encoder API, not an LLM prompt, is the target.

**Seam mode is PER-SEAM, not one global switch (decided F23, extended to the 4th seam at F-P0):**
the original single `SEAMS_MODE=fake|real` flag is refined into independent switches — parser,
embedder, llm, and (once `Reranker` shipped) reranker each resolve `fake`/`real` on their own
(`PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE`/`RERANKER_MODE`). This lets the real parser be validated
against real documents while the embedder and LLM stay on fakes (no API cost/keys needed for that
validation). **Default for every seam, in every environment, remains `fake`** — real is opt-in per
seam, never the app default, and the offline Testcontainers CI suite always runs fully fake
regardless of what's configured locally. `RERANKER_ENABLED` is additionally its own separate gate
from `RERANKER_MODE` (see "The 4 seams" above) — a pattern unique to the reranker, since calling it
at all (not just which mode) changes retrieval behavior.

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

## Retrieval pipeline (multiple flag-gated strategies, all shipped, all additive over the MVP flat path)

Real current shape (`app/services/retrieval/service.py`'s `RetrievalService`, `app/services/chat/
broad_query.py`, `app/services/chat/service.py`), not the original MVP-only sketch:

```python
async def ask(ctx, req) -> ChatResponse:
    scope = resolve_notebook_scope(ctx, req.notebook_id)   # notebook ∩ Access-Roles-allowed docs

    if BROAD_QUERY_ENABLED:                                  # P1, 2026-07-29 — one cheap LLM
        if is_broad_query(req.query, scope):                 # classifier call; falls through to
            return await try_broad_query(ctx, scope, req)    # flat/hybrid/rerank on any fallback
                                                               # (no section summaries, doc count
                                                               # over BROAD_QUERY_MAX_DOCUMENTS)

    qvec = await embedder.embed([req.query])
    # _search_hits: coarse-to-fine section->chunk kNN if HIERARCHICAL_RETRIEVAL_ENABLED, else flat;
    # ALSO widens + RRF-fuses in a lexical (BM25-style) candidate list if HYBRID_SEARCH_ENABLED —
    # orthogonal to flat vs. hierarchical.
    hits = await _search_hits(ctx, qvec, scope, candidate_k, k)
    if RERANKER_ENABLED:                                     # P0, 2026-07-28
        hits = await reranker.rerank(req.query, hits, final_k)   # falls back to unreranked hits on
                                                                   # SeamTransientError, never fails
                                                                   # the whole turn (2026-07-30 fix)
        if hits[0].rerank_score < RERANK_MIN_SCORE:            # confidence gate — no separate flag,
            return weak_evidence_response()                    # inert whenever reranker is off

    return await generate_answer(assemble_context(hits[:8]))    # numbered, cited
```

`FLAGS.graph_retrieval` (V3, knowledge-graph) is still unbuilt — the only strategy in the original
sketch that hasn't shipped.

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
        name text,
        path text,       -- [now] NON-AUTHORITATIVE display cache (F25, 2026-07-12: was a true
                          -- materialized path; parent_id/adjacency-list is now authoritative,
                          -- path is synchronously rebuilt from parent_id+name on every
                          -- move/rename via _rebuild_subtree_paths — never sliced from the old
                          -- string, which risks false-matching a sibling name prefix)
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

### Tables shipped since the original MVP sketch above (all additive, no rewrites of the tables
### above — full column-by-column detail lives in memory.md's "Schema quick-reference")
```sql
-- P0 hybrid search (migration 0021): chunks gained content_tsv (STORED generated tsvector +
-- GIN index) — needs Computed(...) in the ORM mapping, see librarydocs.md "Hybrid search".

-- access-based RBAC (migration 0012, 2026-07-12): a tag only gates access once GRANTED to a
-- role — untagged/ungranted resources stay open to everyone.
access_roles(id, org_id, name)
user_access_roles(user_id, access_role_id)
access_role_tags(access_role_id, tag_id)     -- which tags a role grants
folder_tags(folder_id, tag_id)               -- inherits to the whole subtree

invite_tokens(id, org_id, user_id, token_hash unique, expires_at, used_at)   -- migration 0016
widgets(id, org_id, knowledge_base_id, name, public_id unique, allowed_origins jsonb,
        is_active, created_by)                                              -- migration 0019
notebook_shares(id, org_id, notebook_id, user_id, created_at)               -- migration 0020,
        -- per-person notebook sharing; creator-only by default, NO owner/admin bypass (the one
        -- resource in this app where the org system role does not see everything)
message_feedback(id, org_id, message_id, user_id, rating, reason_tags text[], comment,
        corrected_answer, unique(message_id, user_id))                      -- migration 0022
golden_questions(id, org_id, notebook_id, question, reference_answer,
        reference_contexts jsonb, source_message_id null)                   -- migration 0023
notebook_overviews(id, org_id, notebook_id unique, content, citations jsonb,
        generated_by, stale bool default false)                            -- migration 0024

-- users gained (auth hardening, migration 0013 + 0018): is_active, failed_login_attempts,
-- locked_until, token_version (bumped to invalidate every other session), name.
-- documents gained (migration 0017): uploaded_by.
-- sections/embeddings' `summary`/`topics`/`owner_type='section'` — labeled [later] above — are
-- now ACTUALLY POPULATED when ENRICHMENT_ENABLED (shipped 2026-07-15), not just designed-for.
```

### How this one schema serves all four retrieval futures
- **Document/flat retrieval (MVP, shipped):** search `embeddings WHERE owner_type='chunk'`, filter
  `document_id IN (notebook ∩ allowed)`.
- **Section retrieval (shipped):** chunks carry `section_id` + offsets → expand a hit to its section or
  parent via the `sections` tree. No re-ingest.
- **Hierarchical/indexed (shipped, flag-gated off by default):** enrichment fills `*.summary` +
  inserts `embeddings(owner_type='section')` into the SAME table (`'document'`-level rows are
  designed-for but not populated by any shipped feature yet). Coarse-to-fine routing needed no
  schema change, exactly as designed.
- **Knowledge graph (V3, NOT built):** extraction would read existing chunks → fill
  `entities/mentions/relationships`, each triple pointing back to `chunk_id` + offsets — possible
  only because provenance was stored in MVP, whenever this is actually built.

---

## MVP / V2 / V3 (strictly additive) — MVP + most of V2 are now SHIPPED, not aspirational
- **MVP — flat RAG, NotebookLM-style. SHIPPED.** Core ingestion path + flat vector retrieval +
  grounded cited chat + tenant isolation (including enforced RLS, F60).
- **V2 — hierarchical / indexed / reranked. MOSTLY SHIPPED**, all flag-gated off by default:
  semantic outline + enrichment backfill (`ENRICHMENT_ENABLED`, 2026-07-15), hierarchical
  coarse-to-fine retrieval (`HIERARCHICAL_RETRIEVAL_ENABLED` — built, but a real-seam eval found
  no measured benefit on a single-document corpus, recommended OFF until a large multi-document
  notebook creates real pressure on flat's precision), the `Reranker` seam
  (`RERANKER_ENABLED`, 2026-07-28), hybrid vector+BM25 search (`HYBRID_SEARCH_ENABLED`,
  2026-07-28), a reranker-score confidence gate (implicit whenever the reranker is on),
  contextual retrieval (`CONTEXTUAL_EMBEDDING_ENABLED`, 2026-07-29 — prepends a section summary
  before re-embedding a chunk, Anthropic's technique). New tables: none for any of these (all
  additive to the MVP schema). Not yet built from the original V2 scope: per-user grants beyond
  Access-Roles' tag-based model, connector-sourced folders.
- **P1 — synthesis-across-documents. SHIPPED** (2026-07-29, flag-gated off by default): a
  broad-query router (`BROAD_QUERY_ENABLED`, one cheap LLM classifier call, falls back to the
  normal flat/hierarchical/hybrid/rerank pipeline on any narrow question) + a shared
  map-reduce retrieval strategy (`services/retrieval/mapreduce.py`) reused by an on-demand
  Notebook Overview artifact (`NOTEBOOK_OVERVIEW_ENABLED`).
- **V3 — knowledge graph / advanced. NOT built.** Create `entities/mentions/relationships`; run
  `extraction` backfill; add `graph_then_vector`; any agentic multi-hop retrieval.

## Build-now vs designed-for-postponed
| Concern | Build now | Postponed (designed-for) |
|---|---|---|
| Tenancy (app-level) | `org_id` on every table + always-on app filter + `tenant_session` plumbing | — |
| Tenancy (enforced RLS) | **DONE (F60, migration `0015`):** unconditional policies + `FORCE RLS` on all 18-tables-at-the-time, `app_user`/`migrator` split, teeth-having isolation test. Every table added since ships its own RLS block. | — |
| Tenancy (isolation model) | shared DB, `org_id` rows | per-tenant DB/schema (Enterprise) |
| Permissions | Access Roles: tag-granted resource access (2026-07-12); per-person notebook sharing, no owner/admin bypass (2026-07-27) | connectors, browsing-endpoint gating beyond tags (V2) |
| Folders/tags | full nav tree + tags; folder-mutation gated by Access Roles too (2026-07-27) | connector-sourced folders |
| Sections | tree + structural fields + summary/topics (**populated** when `ENRICHMENT_ENABLED`) | — |
| Embeddings | `owner_type='chunk'` always; `'section'` populated when `ENRICHMENT_ENABLED` | `'document'`-level rows |
| KG tables | NOT migrated; shape decided | created + extraction (V3) |
| Retrieval | flat vector, hierarchical, hybrid (BM25+vector RRF), reranked, broad-query map-reduce — all flag-gated, composable | graph (V3) |
| Reranker | **SHIPPED (2026-07-28):** self-hosted BGE-reranker-v2-m3 via TEI, `RERANKER_ENABLED` (dev/local) | **Locked (2026-08-04):** hosted cross-encoder API (Cohere/Jina/Voyage) for production — same `Reranker` Protocol, new adapter only |
| Eval framework | golden-question set (`golden_questions` table) + admin curation UI shipped (2026-07-28); Ragas metrics grading harness written but **currently broken** (upstream ragas↔langchain_community incompatibility, unfixed as of 2026-07-30) | fixing the Ragas dependency chain |
