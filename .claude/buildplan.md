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
- **F05 LocalDiskObjectStore (offline-first storage) — DONE (built out of order).** Surfaced now,
  well after Phase 1–5, to unblock the F51 manual acceptance gate: this dev environment has no R2
  creds, so a real upload 500s in boto3, and a FastAPI `dependency_overrides` shim can't fix it —
  the arq worker calls `get_object_store()` directly and never sees the override, so its
  parse/structure storage calls would still build real R2 and 500 mid-walk. Adds a filesystem
  implementation of the existing `ObjectStore` put/get port (NOT a new abstraction — second impl of
  an existing port; no `exists`/`delete`, those ride with the future orphan-sweep feature that needs
  them) selected by `STORAGE_MODE=r2|local` (default `r2` — production unchanged, mirroring the seam
  `*_MODE` fake-default). `get_object_store()` is the single selection point, so API + worker share
  one store. Deliberately not production-durable (no multipart/concurrency/fsync). Completes the
  DI'd storage abstraction (one hardcoded impl before) and extends the offline-first principle
  (seam fakes default, tests run offline) to storage, so the full product runs with zero cloud creds.
  - *DoD:* `LocalDiskObjectStore` satisfies the same put/get contract as R2 — an offline temp-dir test
    proves write→read round-trip + nested-key-dir creation + **get-raises-on-missing** (the real
    parity property: the ingestion callers catch `except Exception`, so local `FileNotFoundError` and
    boto3 `ClientError` take the same not-found branch); factory selects on `STORAGE_MODE`, default
    stays `r2`; CI-offline invariant preserved (no creds, no Docker). (`8c44c75`.)

## Phase 1 — Identity + Documents
- **F10 Auth — DONE.** signup (creates org + owner), login (multi-org aware), refresh, logout,
  `/me`, invite teammates, list org users, patch user role.
  - Role enum: **`owner | admin | member`** — org creator → `owner`; invites default → `member`.
  - Session mechanism: credential → **short-lived JWT access token + httpOnly refresh cookie**,
    validated by the `current_user` dependency → `TenantContext`.
  - *DoD met:* a user signs up, creates an org (becomes `owner`), logs in; sessions scoped to org;
    an invited teammate joins as `member`. See `app/identity/*` (pre-MVC-refactor path —
    now spread across `app/{models,schemas,controllers,services,repositories,exceptions}/`
    under the `auth` domain name; see memory.md "MVC layout refactor"), migration
    `0003_auth_password_hash`, `tests/test_auth.py` (217 lines).
- **F11 Folders + tags:** CRUD for the folder tree (materialized path) and tags.
  - *DoD:* create nested folders; tag a document; list by folder/tag.
- **F12 Upload + dedupe:** upload to object store, checksum, `unique(org_id, checksum)`, status=`uploaded`.
  - *DoD:* re-uploading an identical file returns the existing document, not a duplicate.
- **F25 Folder move/rename/delete:** added late, **out of numeric order** — same shape as
  F24, surfaced by a client requirement (move/rename/delete must exist; folders are heavily
  navigated and deeply nested) discovered well after F11 shipped. F11's folder model was
  materialized-path-only with no move/rename and an unconditional cascading delete (a live
  data-loss bug, not just a missing feature). Resolved via an explicit analysis+design
  session (parent-pointer model chosen over materialized-path, see memory.md "F25" for the
  full reasoning) before any code was written.
  - *DoD:* rename and move correctly reject cycles and name collisions (target-scoped,
    same-transaction checks, unique-constraint backstop on race); every descendant's
    display-cache `path` is rebuilt correctly (multi-generation depth, no false match on a
    sibling sharing a name prefix); delete defaults to block-if-non-empty (409), with
    explicit `cascade` (DB-driven, documents survive orphaned to root) and `reflow`
    (direct children move up to the deleted folder's parent) modes; full org isolation.
  - **V2 folder-permissions note (do NOT build yet):** the client separately described
    per-folder role-based access as a real future need. The parent-pointer model was chosen
    partly because it gives that future layer a stable `folder_id` to key grants on (never
    invalidated by a move), unlike a path-string scheme. No permission code exists yet.

## Phase 2 — Ingestion core path
- **F20 parsing stage:** parser seam → persist raw text + structure artifact; set language/page_count.
  - *DoD:* a real PDF moves `UPLOADED→PARSING→STRUCTURING` (status enum from `documents/status.py`;
    there is no `parsed` state); artifacts in object store; `language`/`page_count` set; failures set
    `status=FAILED` + `failed_stage`.
- **F21 structuring stage:** build `sections` tree (structural only) + `chunks` with offsets + `section_id`.
  - *DoD:* sections reflect the document outline; chunks carry valid char offsets; idempotent re-run.
- **F22 embedding stage:** embed chunks, upsert `embeddings(owner_type='chunk')`, status→`ready`.
  - *DoD:* re-running embedding does not duplicate rows; a `ready` doc is queryable.

## Phase 2.5 — Real-parser validation
F00–F22 ran the entire pipeline against `FakeParser` only. Before building Phase 3+ (retrieval,
notebooks, chat) on top of the structuring/chunking/embedding contract, validate that a REAL
parser slots into the same `Parser` seam with zero changes downstream. Cheap to find a contract
mismatch now; expensive after Phase 3+ depends on it.
- **F23 Real parser integration:**
  - *DoD:*
    - Real parser adapter implements the EXISTING `Parser` port — same output shape as the fake:
      extracted text, structural blocks (heading depth + section boundaries), provenance
      (page/offset signal the structuring stage turns into char offsets).
    - Seam mode becomes PER-SEAM (`SEAMS_MODE` split into `PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE`
      or equivalent): parser can be `real` while embedder + llm stay `fake`; the full test suite
      stays fully fake and deterministic. Real is opt-in, never the app default.
    - An opt-in integration path (script or env-gated pytest marker) runs
      upload→parse→structure→chunk→embed on one real file and dumps the section tree + chunks +
      offsets for manual inspection. It MUST NOT run in the offline Testcontainers CI suite
      (network + cost).
    - Real failures (timeout, unsupported format, scanned-no-OCR, encrypted, oversized) map onto
      the existing stage failure model (`status=FAILED` + `failed_stage` + `error_detail`) — no
      uncaught exceptions.
    - **Acceptance gate:** swapping fake→real requires ZERO changes to structuring/chunking/
      embedding. If it does, that mismatch IS the finding — stop and fix the contract before F30.
    - One real document reaches `READY` on real-parser output; CI stays fake-only and green.
  - *Defer (do NOT build here):* semantic enrichment (V2), OCR tuning, multi-provider fallback.
- **F24 Ingestion auto-dispatch (arq):** added late, **out of numeric order** — built in the
  same session as F51's frontend recon (which is chronologically after F41), because that
  recon surfaced that nothing had ever auto-advanced a document past `UPLOADED`: F20-F22's
  stage endpoints were always manual-trigger only, and no arq task had ever been registered.
  Without this, F51's upload UI would have nothing to observe — a document would sit at
  `UPLOADED` forever. Upload enqueues the parsing stage's job; each stage's job, on success,
  enqueues the next — server-side, resumable, independent of the uploading client (the
  frontend was deliberately NOT made to drive this chain — see memory.md for why).
  - *DoD:* a successful upload reaches `READY` (or `FAILED`) with no manual `/ingestion/*`
    calls; a checksum-dedupe hit does not re-trigger the pipeline; redelivery of any stage's
    job (arq is at-least-once) never double-enqueues the next stage, including under
    concurrent redelivery (guaranteed via a deterministic arq `job_id`, not just an
    application-level status check — see memory.md for a redelivery race this session found
    and fixed).
  - *Known, accepted gap (do NOT build here):* if the enqueue call itself fails — at upload
    time or between stages — the chain silently stops and the document is stranded. No
    sweeper/re-dispatch is built; a future re-dispatch endpoint or sweeper job would close it.

## Phase 3 — Knowledge + Retrieval
- **F30 Notebooks:** create notebook; add/remove documents by reference (join table).
  - *DoD:* a document in two notebooks has exactly one set of chunks/embeddings.
- **F31 Flat retrieval:** `retrieve()` with `flat_vector`, scoped by `org_id` and `notebook ∩ allowed`.
  - *DoD:* retrieval returns only chunks from the notebook's docs; isolation test passes.

## Phase 4 — Chat
- **F40 Grounded generation:** assemble numbered context, strict "answer only from context / else
  say you don't know" prompt. Non-streaming for this feature — see F4x below for SSE (amended
  2026-06-24: buildplan originally said "stream over SSE" here; deferred, see F4x).
  - *DoD:* answers never use outside knowledge; refuses when sources don't cover the question.
  - **Manual acceptance gate (required, automated green ≠ done):** a human must ask the REAL LLM
    (not the fake) a question the notebook's corpus cannot answer, against a real notebook with
    real content, and confirm it refuses with the "I don't have that in the provided sources"
    contract rather than inventing an answer from outside knowledge. This is the product's core
    behavioral promise and the fake LLM cannot certify it (see memory.md "F40 Grounded
    generation" for why).
- **F4x SSE streaming for chat:** switch `POST /chat/ask` to Server-Sent Events, streaming the
  answer token-by-token. Deferred out of F40 because no consumer exists yet (F52 chat UI isn't
  built) and F40's substance (grounding discipline, retrieval wiring, retry/failure handling) is
  transport-independent. F40's `chat.service` already exposes a streaming-ready async-generator
  core (`generate_answer`) so this should be a router-only change, not a service rewrite.
  - *DoD:* same grounding/citation contract as F40, delivered incrementally over SSE.
- **F41 Citations:** map answer markers back to chunk offsets; store in `messages.citations`.
  - *DoD:* clicking a citation shows the exact source span.
- **F42 Debug bundle (admin):** persist retrieved hits + scores + final prompt + raw output to
  `message_traces` (one row per answer); expose read-only to `role in (owner, admin)`.
  - *DoD:* an admin can see why any answer was produced (from the persisted trace, not recomputed).

## Phase 5 — Frontend SPA
- **F50 App shell + auth UI — DONE OUT OF SEQUENCE (direct instruction, before Phase 2-4).**
  Built early per a direct senior instruction rather than waiting for the normal phase order.
  Vite React app scaffolded (`frontend/{package.json,vite.config.ts,tailwind.config.js,...}`),
  `App.tsx` + `ProtectedRoute` + `lib/auth.tsx`/`lib/api.ts`, `AppShell`/`Sidebar`/`HomePage` shell,
  `LoginPage`/`SignupPage` wired to the F10 auth endpoints.
  - *DoD met:* a user can sign up, log in, and land in the app shell against the real F10 backend.
- **F51 Repository (folders/tags/upload + status) — PARTIAL / IN PROGRESS.** Only the
  auth-adjacent slice landed as part of the F50 pull-forward: `UsersPage.tsx` (org user list +
  role management UI, calling F10's `/invite` / `/users` / `/users/{id}/role`) and `DocsPage.tsx`
  (placeholder panel, not yet wired to F11/F12 — those backend features don't exist yet).
  Folders/tags/upload UI itself is **not started** — was blocked on F11/F12 landing first;
  F11/F12 are done, and F24 (ingestion auto-dispatch) now also landed so an upload actually
  advances to `READY` for the UI to observe. Also introducing a minimal Vitest+RTL test
  harness as part of this feature (the first real data-fetching frontend feature) — tests
  mock the typed API client, never the real backend, wired into CI alongside the backend
  suite.
- **F52 Notebook + chat UI with streaming + citations.** Not started — blocked on Phase 3/4.
  - *DoD:* a non-technical user completes the full core flow end-to-end.

> Note: F50/F51 were pulled forward out of normal phase order specifically for the auth-facing
> slice. Resume the rest of F51 (folders/tags/upload) only after F11 + F12 land on the backend —
> don't build more frontend ahead of the backend it depends on.

## Phase 6 — Security Hardening (AFTER MVP is validated, BEFORE real customer data)
The schema + plumbing exist from Phase 0; this phase turns on the teeth.
- **F60 Enforced RLS:** set `RLS_ENABLED=true`; create the restricted **`app_user`** role and the
  privileged **`migrator`** role (Alembic/owner) split; the app connects as `app_user`; `ENABLE` +
  `FORCE ROW LEVEL SECURITY` on every tenant table; policies use `current_setting('app.org_id', true)`.
  - *DoD:* the **teeth-having** isolation test — connect as the restricted `app_user`, **omit** the
    app-level filter, and still read **zero** cross-tenant rows (RLS alone blocks the leak).

---

## Unplanned additions — needs a decision
- **`GET /context/docs` + `/context/docs/{path}`** — **RESOLVED: deleted** (2026-06-23).
  Decision was "remove" — exposed internal `.claude/`/`CLAUDE.md` build docs to any
  authenticated user across all orgs (not org-scoped), too risky to formalize as a real
  feature. Removed `backend/app/platform/context_docs.py`, its router wiring in `main.py`,
  the `CONTEXT_DOCS_ROOT` config field, the `test_context_docs_list` test, and the frontend
  docs-viewer (`DocsPage.tsx`, `MainPanels.tsx`, `contextApi`/`DocEntry`/`DocContent` in
  `lib/api.ts`, the `/app/docs` route + `Library` nav item, the `/context` Vite proxy entry,
  the `react-markdown` dependency). Auth UI + plain app shell kept intact.

## Postponed (designed-for — see architecture.md, do NOT build yet)
- V2: enrichment backfill, hierarchical retrieval strategies, reranker seam, groups/grants permissions, connectors.
- V3: KG tables + extraction backfill, graph retrieval, hybrid search, public embeddable chatbot.
- Enterprise: SSO/SAML/SCIM, audit logs, per-tenant DB, data residency.

**Demoable milestone:** end of Phase 4 = a working NotebookLM-style company knowledge base.
