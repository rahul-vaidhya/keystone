# buildplan.md — Sequenced Roadmap

Build in dependency order. Each feature lands **with its tests** before the next.
Every feature has a **Definition of Done (DoD)**. Do not start a feature until the previous
one's DoD is met. Keep features small enough for one focused session.

---

## Phase 0 — Platform skeleton (the foundation everything stands on)
- **F00 Repo + layout:** modular folder structure, pydantic-settings config, structlog logging.
  - *DoD:* app imports; `GET /health` returns 200; config loads from env.
- **F01 DB + migrations:** async SQLAlchemy, Alembic, organizations + users tables.
  - *DoD:* migration creates tables; a smoke test inserts/reads an org.
- **F02 Tenant isolation (app-level now; enforced RLS deferred to Phase 6):** `org_id` on every
  tenant-scoped table (incl. join/child tables); base-repository app-level scoping; the
  `tenant_session(org_id)` helper (request path + worker jobs via the job payload); the RLS policies +
  `app_user`/`migrator` role split **written in a migration but flag-OFF** (`RLS_ENABLED=false`).
  - *DoD:* an **app-level** isolation integration test — two orgs, app filter on — proves org A cannot
    read org B's rows. (The restricted-role, filter-omitted "teeth" test is Phase 6, not here.)
- **F03 Seams + fakes:** `Parser`/`Embedder`/`LLM` Protocols in `platform/`, one real + one fake each.
  - *DoD:* the whole suite runs with fakes, no API keys.
- **F04 CI:** test suite runs against an ephemeral Testcontainers Postgres on every push.
  - *DoD:* green pipeline on a trivial PR.

## Phase 1 — Identity + Documents
- **F10 Auth:** signup, login, org creation, invite teammates (email/magic-link or password).
  - Role enum: **`owner | admin | member`** — org creator → `owner`; invites default → `member`.
  - Session mechanism: credential → **short-lived JWT access token + httpOnly refresh cookie**,
    validated by the `current_user` dependency → `TenantContext`.
  - *DoD:* a user signs up, creates an org (becomes `owner`), logs in; sessions scoped to org;
    an invited teammate joins as `member`.
- **F11 Folders + tags:** CRUD for the folder tree (materialized path) and tags.
  - *DoD:* create nested folders; tag a document; list by folder/tag.
- **F12 Upload + dedupe:** upload to object store, checksum, `unique(org_id, checksum)`, status=`uploaded`.
  - *DoD:* re-uploading an identical file returns the existing document, not a duplicate.

## Phase 2 — Ingestion core path
- **F20 parsing stage:** parser seam → persist raw text + structure artifact; set language/page_count.
  - *DoD:* a real PDF moves `UPLOADED→PARSING→STRUCTURING` (status enum from `documents/status.py`;
    there is no `parsed` state); artifacts in object store; `language`/`page_count` set; failures set
    `status=FAILED` + `failed_stage`.
- **F21 structuring stage:** build `sections` tree (structural only) + `chunks` with offsets + `section_id`.
  - *DoD:* sections reflect the document outline; chunks carry valid char offsets; idempotent re-run.
- **F22 embedding stage:** embed chunks, upsert `embeddings(owner_type='chunk')`, status→`ready`.
  - *DoD:* re-running embedding does not duplicate rows; a `ready` doc is queryable.

## Phase 3 — Knowledge + Retrieval
- **F30 Notebooks:** create notebook; add/remove documents by reference (join table).
  - *DoD:* a document in two notebooks has exactly one set of chunks/embeddings.
- **F31 Flat retrieval:** `retrieve()` with `flat_vector`, scoped by `org_id` and `notebook ∩ allowed`.
  - *DoD:* retrieval returns only chunks from the notebook's docs; isolation test passes.

## Phase 4 — Chat
- **F40 Grounded generation:** assemble numbered context, strict "answer only from context / else
  say you don't know" prompt, stream over SSE.
  - *DoD:* answers never use outside knowledge; refuses when sources don't cover the question.
- **F41 Citations:** map answer markers back to chunk offsets; store in `messages.citations`.
  - *DoD:* clicking a citation shows the exact source span.
- **F42 Debug bundle (admin):** persist retrieved hits + scores + final prompt + raw output to
  `message_traces` (one row per answer); expose read-only to `role in (owner, admin)`.
  - *DoD:* an admin can see why any answer was produced (from the persisted trace, not recomputed).

## Phase 5 — Frontend SPA
- **F50 App shell + auth UI.** **F51 Repository (folders/tags/upload + status).**
  **F52 Notebook + chat UI with streaming + citations.**
  - *DoD:* a non-technical user completes the full core flow end-to-end.

## Phase 6 — Security Hardening (AFTER MVP is validated, BEFORE real customer data)
The schema + plumbing exist from Phase 0; this phase turns on the teeth.
- **F60 Enforced RLS:** set `RLS_ENABLED=true`; create the restricted **`app_user`** role and the
  privileged **`migrator`** role (Alembic/owner) split; the app connects as `app_user`; `ENABLE` +
  `FORCE ROW LEVEL SECURITY` on every tenant table; policies use `current_setting('app.org_id', true)`.
  - *DoD:* the **teeth-having** isolation test — connect as the restricted `app_user`, **omit** the
    app-level filter, and still read **zero** cross-tenant rows (RLS alone blocks the leak).

---

## Postponed (designed-for — see architecture.md, do NOT build yet)
- V2: enrichment backfill, hierarchical retrieval strategies, reranker seam, groups/grants permissions, connectors.
- V3: KG tables + extraction backfill, graph retrieval, hybrid search, public embeddable chatbot.
- Enterprise: SSO/SAML/SCIM, audit logs, per-tenant DB, data residency.

**Demoable milestone:** end of Phase 4 = a working NotebookLM-style company knowledge base.
