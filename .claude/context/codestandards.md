# codestandards.md — How Code Must Be Written

## Language & style
- Python 3.12+, **fully type-hinted**. `async def` for anything doing IO (DB, HTTP, queue).
- Naming: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE` constants.
  Modules/files `snake_case`. Pydantic models `PascalCase` ending in intent (`...Create`, `...Out`).
- Format with `ruff format`; lint with `ruff`. No unused imports, no bare `except:`.

## Layering (enforced — this is what prevents drift)

Layer-first MVC: code is grouped by role into `app/{models,schemas,controllers,services,
repositories,exceptions,tasks}/`, with one file (or subpackage) per domain inside each
layer package. Full layout + domain-naming quirks: `architecture.md` "Folder map."

- `controllers/<domain>.py`: HTTP only — parse/validate request (Pydantic), call a
  `services/<domain>.py` function, shape the response. **No SQL. No business logic.**
- `services/<domain>.py` (or `services/<domain>/`): use cases and business rules. Knows
  nothing about FastAPI or raw SQL. Reaches other domains **only via their
  `services/<other>.py`**, and external systems **only via a seam**.
- `repositories/<domain>.py` (or `repositories/<domain>/`): **all SQL lives here.** Every
  method takes/uses the tenant's `org_id`. Returns domain objects, not ORM rows leaking
  upward.
- `tasks/<domain>.py`: arq task functions; thin — they call into the domain's
  `services/<domain>.py`.

```python
# RIGHT — controller delegates, service holds logic, repo holds SQL
@router.post("/documents")
async def upload(req: DocumentCreate, ctx: TenantContext = Depends(get_ctx)):
    return await documents_service.upload(ctx, req)

# WRONG — SQL + business logic in the controller
@router.post("/documents")
async def upload(req, db):
    if await db.execute(select(Document).where(...)):   # ❌ SQL in controller
        ...                                              # ❌ logic in controller
```

## Tenancy (security-critical)
- **Every tenant-scoped table carries `org_id`, including join and child tables** (`document_tags`,
  `knowledge_base_documents`, `messages`, `message_traces`). **No "scope via parent join" exceptions** —
  the repository must always be able to filter `org_id` directly on the table it queries.
- Every repository query filters by `org_id` from `TenantContext` / the job payload. Never trust an
  `org_id` from the request body — take it from the authenticated context. **This app-level filter is
  always on**, independent of any flag.
- Open DB work through the shared **`tenant_session(org_id)`** helper (request path AND arq workers).
- RLS is a backstop, **not** a license to skip the application filter. Enforced RLS (the restricted
  `app_user` role + `FORCE ROW LEVEL SECURITY`) is **deferred to Phase 6**, gated by `RLS_ENABLED`
  (default OFF in dev/test). See architecture.md "Tenancy plumbing."

```python
# RIGHT
async def list_for_org(self, ctx) -> list[Document]:
    return await self._db.scalars(select(Document).where(Document.org_id == ctx.org_id))
# WRONG — unscoped query
async def list_all(self): return await self._db.scalars(select(Document))   # ❌ leaks tenants
```

## External calls
- Only through `Parser` / `Embedder` / `LLM` seams. Never import a vendor SDK inside a `service`.
- Inject the seam (constructor or FastAPI dependency) so tests pass a fake.

## Ingestion correctness
- Stages are **idempotent**: deterministic IDs (`chunk_id = sha256(document_id|ordinal|content)`)
  + `unique(...)` constraints; writes are upserts.
- Persist each stage's output (raw text, structure, chunks) before advancing status.
- On failure: set `status='failed'`, `failed_stage`, `error_detail` — never swallow the error.

## Errors & logging
- Raise typed domain exceptions in services; map to HTTP in a single exception handler.
- Structured logs (structlog) carrying a `request_id` generated at the edge and propagated into the
  arq job payload, so an upload's request and its background job share one trace.
- `try/except` only around IO/seam calls, with context in the log; re-raise as a domain error.

## Tests (land with the code)
- **Unit:** pure logic (chunker, citation mapper, `assemble_context`) with fakes — milliseconds.
- **Integration:** services + repositories against a **real** ephemeral Postgres+pgvector
  (Testcontainers). Fake the 3 seams. Always include a tenant-isolation assertion.
- **Do not mock the database.** Fake only what is slow or nondeterministic (the seams).

## Don'ts
- No business logic in controllers; no SQL outside repositories; no cross-domain repository access.
- No vendor SDK in services. No unscoped queries. No silent failures. No new seam beyond the 3
  (Reranker arrives in V2). No premature dedicated vector DB / KG / agentic retrieval.
