# Embeddable Website Chatbot Widget — Implementation Plan

> Status: **APPROVED, NOT YET BUILT** (plan produced 2026-07-23 via `/architect`; user
> confirmed all four scoping decisions). This document is self-contained: a fresh
> session should be able to build the feature from this file plus the standard
> project context (`.claude/orchestrator.md`, `.claude/context/architecture.md`,
> `.claude/context/codestandards.md`).
>
> Precedent for this doc: `docs/access-roles-dnd-plan.md`.

---

## 1. What we are building

An org admin picks a notebook and creates a **widget**: Veratas generates a
paste-able `<script>` snippet (and a raw iframe URL). On the customer's external
website the script injects a floating chat bubble; clicking it opens an iframe of a
minimal chat page hosted by the Veratas SPA. Visitors chat **anonymously** with that
one notebook — grounded, cited, streamed over SSE — through a new public backend
endpoint protected by a **domain allowlist** and **per-widget / per-IP rate limits**.
Conversations are persisted and marked as widget-originated. Admins can list,
revoke, and reconfigure widgets in a new SPA page.

This is the Intercom/Chatbase-class embed pattern: script tag → bubble → iframe on
our origin, secured by origin allowlisting + rate limiting (an embed ID in a
third-party page is inherently public; revocation + rate limits are the real cost
backstops).

## 2. Language / vocabulary

- **Widget** — one embeddable chatbot = one notebook + one public ID + an
  allowed-origins list + an active flag. Table: `widgets`.
- **Snippet / "the link"** — the `<script>` tag (and iframe URL) containing the
  public widget ID. **Not a secret** — it is visible in the customer's page source.
- **Visitor** — an anonymous end user on the customer's site; never has a Veratas
  account. Backend context: `TenantContext(org_id=...)` with `user_id=None`,
  `role=None` (the established worker/accept-invite pattern,
  `app/middleware/context.py`).

## 3. Decisions made (confirmed by the user 2026-07-23 — do not relitigate)

1. **Embed form: script bubble + iframe** (also expose the raw iframe URL for
   free). `widget.js` is a small hand-written vanilla-JS file in
   `frontend/public/` — **no second Vite build entry** (the current build is
   single-entry; a widget bundle entry is not needed for a static vanilla file).
2. **Security: allowlist + rate limit.** `widget.js` captures the parent page's
   `window.location.origin` and passes it into the iframe as a query param; the
   embed page includes it on every chat request; backend rejects requests whose
   claimed origin is not in the widget's `allowed_origins`. Plus Redis
   fixed-window rate limits (per-widget and per-IP). Named, accepted caveat: a
   non-browser client can spoof the claimed origin — rate limiting and instant
   revocation (`is_active=false`) are the backstops. This matches industry
   practice (Chatbase "Connect", Botpress "Preventing Abuse").
3. **Persistence: reuse `conversations`/`messages`/`message_traces`**, with a new
   nullable `conversations.widget_id` column marking widget traffic. Debug traces
   keep working for widget answers. `ConversationRepository.create` already
   accepts `user_id=None` (`services/chat/repository.py:20-28`).
4. **Full slice**: backend + embed page + `widget.js` + admin "Embed widgets" SPA
   page. Shippable to a real customer.
5. **Public URL carries `org_id` + widget `public_id`** — same precedent as
   accept-invite links (org_id is a routing identifier, not a secret;
   `app/services/auth.py` `accept_invite` builds
   `TenantContext(org_id=req.org_id)` directly). No new RLS bootstrap machinery
   (no new GUC / no `auth_session`-style policies) needed.
6. **Streaming**: the embed page reuses the existing SSE client pattern
   (fetch + ReadableStream + TextDecoder, see `frontend/src/services/chatService`
   / `http.ts` precedent) against a new public stream endpoint that calls the
   **existing** `ChatService.stream_ask` with an anonymous ctx. No changes to the
   chat pipeline itself.
7. **Widget `public_id` stored in plaintext** (unique-indexed
   `secrets.token_urlsafe(16)` or similar) — unlike invite tokens it is public by
   design, so sha256-at-rest buys nothing. (Invite-token hashing precedent at
   `app/services/auth.py:407-408` deliberately NOT copied here.)

## 4. Load-bearing recon facts (verified 2026-07-23, with citations)

- **Anonymous org-scoped context is first-class**: `TenantContext`
  (`app/middleware/context.py:16-20`) defaults `user_id`/`role` to `None`;
  `accept_invite` (`app/services/auth.py:442`) already builds
  `TenantContext(org_id=req.org_id)` on a public endpoint.
- **Public endpoints precedent**: signup/login/refresh/logout/accept-invite are
  registered with no auth dependency (`controllers/auth.py:48-90`,
  `routes/auth.py:18-20`). The new public embed endpoints follow this shape.
- **Chat pipeline**: `ChatRequest` = `{notebook_id: UUID, query: str, k: int=8
  (1..50)}` (`app/models/chat.py:108-111`). `ChatService.ask`
  (`services/chat/service.py:204-254`) and `stream_ask` (`:296-352`) take
  `(ctx, req, embedder=, llm=, correlation_id=)`. `_persist` (`:256-294`) writes
  Conversation + user Message + assistant Message + MessageTrace in one
  `tenant_session(ctx.org_id)` transaction per call.
- **Retrieval scoping**: `RetrievalService.search`
  (`app/services/retrieval.py:102-122`) scopes to notebook-docs ∩
  `resolve_allowed_documents(ctx)`. With `role=None`/`user_id=None`, an org with
  NO access-controlling tags returns every document (common case, fine); with
  tag-gating configured, anonymous ctx should resolve to zero granted tags →
  tag-gated docs invisible (the SAFE direction). **This behavior must be pinned
  with a test, not assumed** — `resolve_user_granted_tags(ctx)` with
  `user_id=None` was not exercised before this feature.
- **No CORS work needed for chat calls**: the embed chat page is served from OUR
  origin (SPA), so its fetches to the backend are same-origin. Only `widget.js`
  crosses origins, and `<script src>` is not subject to CORS.
  `main.py:9-34` CORS config (`CORS_ORIGINS` allowlist + credentials) stays
  untouched.
- **No rate limiting exists anywhere in the app today** — must be built new.
  Redis is available (`REDIS_URL` in settings, arq already uses it).
- **Settings gap**: there is no `PUBLIC_BASE_URL`/frontend-URL setting anywhere
  (`app/config/settings.py`) — the snippet builder needs a new `PUBLIC_APP_URL`.
- **Frontend auth pattern is unusable in the iframe** (sessionStorage JWT +
  refresh cookie, `frontend/src/services/http.ts:3-12,58-64`) — the embed page
  must use a bare fetch wrapper with no Authorization header and no
  `credentials: "include"`.
- **Notebooks have no public/shared flag** (`app/models/knowledge.py:21-43`) —
  embeddability lives entirely on the new `widgets` table, not on
  `knowledge_bases`.
- **Migration chain head is `0018`** — this feature takes **`0019`**
  (`down_revision = "0018"`).
- **Domain template**: copy the auth domain shape — `models/<d>.py` (ORM +
  `# ---- API schemas ----`), `services/<d>.py` (`# ---- exceptions ---- /
  # ---- repository ---- / # ---- service ----`, module-level singleton),
  `controllers/<d>.py` (thin handlers), `routes/<d>.py`
  (`router.<verb>(path, ...)(handler)` call form), register in
  `backend/main.py:37-43`, map typed exceptions in `app/utils/http.py`.
- **RLS rule (locked)**: every new tenant table ships its own migration block:
  `ENABLE` + `FORCE ROW LEVEL SECURITY`, `tenant_isolation` policy with the
  load-bearing `NULLIF(current_setting('app.org_id', true), '')::uuid` cast, and
  `GRANT SELECT, INSERT, UPDATE, DELETE ... TO app_user` — copy
  `migrations/versions/0016_invite_tokens.py:79-89` exactly.

## 5. Schema (migration `0019_widgets.py`)

New table `widgets`:

| column | type | notes |
|---|---|---|
| id | uuid PK | server default gen |
| org_id | uuid FK organizations ON DELETE CASCADE, NOT NULL | indexed |
| knowledge_base_id | uuid FK knowledge_bases ON DELETE CASCADE, NOT NULL | one widget = one notebook |
| name | text NOT NULL | admin-facing label |
| public_id | text NOT NULL, UNIQUE (global, not per-org) | `secrets.token_urlsafe`, plaintext |
| allowed_origins | JSONB (list[str]) NOT NULL default `[]` | exact origins, e.g. `https://example.com`; empty list = reject all (admin must add at least one) OR treat empty as allow-all — **decide at build time, recommend: empty = allow-all with a UI warning**, so trying the widget locally isn't a wall |
| is_active | boolean NOT NULL default true | instant revocation switch |
| created_by | uuid FK users ON DELETE SET NULL, nullable | mirrors `documents.uploaded_by` (0017) |
| created_at / updated_at | timestamptz | `updated_at` set explicitly in repo update (MissingGreenlet gotcha — never `onupdate=func.now()`) |

Plus: `ALTER TABLE conversations ADD COLUMN widget_id uuid NULL REFERENCES
widgets(id) ON DELETE SET NULL`. (New COLUMN on an already-RLS-protected table
inherits the table policy — no new RLS statements for `conversations`, per the
0017 precedent. The new `widgets` TABLE does need its own full RLS block.)

## 6. Backend build steps

1. **`app/models/embed.py`** — `Widget` ORM model + API schemas:
   `WidgetCreateRequest {knowledge_base_id, name, allowed_origins}`,
   `WidgetUpdateRequest {name?, allowed_origins?, is_active?}`,
   `WidgetOut {id, name, knowledge_base_id, public_id, allowed_origins,
   is_active, created_at}` (+ snippet/iframe URL fields built from
   `PUBLIC_APP_URL`), public-facing `EmbedConfigOut {widget_name, notebook_name?}`,
   `EmbedChatRequest {query, k?, parent_origin}`.
   Register the model import in `migrations/env.py`'s model-import list.
2. **`app/models/chat.py`** — add nullable `widget_id` to the `Conversation` ORM
   model.
3. **`app/services/embed.py`** — exceptions (`EmbedError`, `WidgetNotFound`,
   `OriginNotAllowed`, `WidgetRateLimited`); `WidgetRepository(BaseRepository)`
   (SQL only: create / list_for_org / get / get_active_by_public_id / update /
   delete — every query org-scoped); `EmbedService`:
   - Admin ops validate the notebook via `knowledge_service.get_notebook(ctx, id)`
     (service accessor, NEVER the knowledge repository — hard rule 1).
   - `public_chat_stream(org_id, public_id, req, ...)`: build
     `TenantContext(org_id=org_id)`; load active widget by public_id (inactive or
     missing → one generic `WidgetNotFound` → 404, anti-enumeration, mirroring
     `InvalidInviteToken`); enforce origin allowlist; enforce rate limits; then
     delegate to `chat_service.stream_ask(ctx, ChatRequest(notebook_id=widget.
     knowledge_base_id, query=..., k=...), embedder=, llm=, ...)` and thread
     `widget_id` into persistence (smallest change: add an optional
     `widget_id=None` kwarg down `stream_ask` → `_persist` →
     `ConversationRepository.create`).
4. **Rate limiter** — small util (e.g. `app/utils/rate_limit.py` or inside
   `services/embed.py`): Redis fixed window `INCR`+`EXPIRE` keyed
   `widget:{id}:{minute}` and `widgetip:{id}:{ip}:{minute}`, limits from settings.
   Must have an injectable/fake implementation so the offline suite never needs
   Redis (same DI treatment as `ObjectStore`/`JobQueue` — config/DI-selected, not
   a seam).
5. **Settings** (`app/config/settings.py`): `PUBLIC_APP_URL`
   (default `http://localhost:5173`), `WIDGET_RATE_LIMIT_PER_MINUTE` (e.g. 30),
   `WIDGET_IP_RATE_LIMIT_PER_MINUTE` (e.g. 10).
6. **`app/controllers/embed.py` + `app/routes/embed.py`** —
   Admin (auth + `require_admin`):
   `POST /embed/widgets`, `GET /embed/widgets`, `PATCH /embed/widgets/{id}`,
   `DELETE /embed/widgets/{id}`.
   Public (NO auth dependency, accept-invite precedent):
   `GET /embed/public/{org_id}/{public_id}/config`,
   `POST /embed/public/{org_id}/{public_id}/stream` (SSE `StreamingResponse`,
   copy the `stream_ask` controller shape at `controllers/chat.py:43-76`).
   Register router in `backend/main.py`; map exceptions in `app/utils/http.py`
   (`WidgetNotFound`→404, `OriginNotAllowed`→403, `WidgetRateLimited`→429).

## 7. Frontend build steps

1. **`frontend/public/widget.js`** — hand-written vanilla JS (ES5-safe, no
   imports). Reads its own `<script>` tag's `data-org` + `data-widget-id`
   (+ optional `data-base-url`); injects a fixed-position bubble button; on click
   toggles an iframe panel pointing at
   `{base}/embed?org={org}&widget={id}&parent={encodeURIComponent(location.origin)}`.
   Zero dependencies, a few KB, inline styles (customer pages have no Tailwind).
2. **`frontend/src/pages/EmbedChatPage.tsx`** — public route `/embed` in
   `App.tsx`, OUTSIDE `ProtectedRoute` and outside `AppShell` (no sidebar). Reads
   `org`/`widget`/`parent` from query params; fetches public config (invalid/
   revoked → friendly "chatbot unavailable" state); slim chat UI (bubbles, typing
   indicator, `[n]` citation markers can render as plain superscripts for MVP —
   full CitationPanel reuse optional). Uses a bare fetch/SSE wrapper (NO
   sessionStorage token, NO `credentials: "include"`), buffering on `\n\n` like
   `chatApi.streamAsk`. `ChatPanel.tsx` is deliberately NOT reused (coupled to
   auth/documents/dialog context).
3. **`frontend/src/pages/EmbedWidgetsPage.tsx`** + `services/embedService.ts` +
   `types/embed.ts` — admin-gated page (route inside `AdminRoute`, new Sidebar
   nav item, mirroring `AccessRolesPage`/`UsersPage`): create widget (notebook
   select from `notebooksApi.list`, name, origins editor), list with
   active/revoked state + revoke/delete (confirm via the existing `useDialog`
   system — never `window.confirm`), and a copy-snippet box showing both the
   script tag and the raw iframe URL. Add `/embed` to `vite.config.ts` dev proxy.
4. **Imprint**: after building, add reusable pieces to
   `.claude/context/uiregistry.md` (`/imprint`).

## 8. Tests (land WITH the code — Definition of Done)

Backend (`tests/test_embed.py`, unique email prefix e.g. `embed-`):
- Admin CRUD happy path; member → 403; cross-org → 404.
- Widget create validates notebook exists (missing → 404).
- Public config endpoint: valid widget 200; unknown public_id 404; revoked 404;
  wrong org_id 404 (one generic error for all — anti-enumeration).
- Public stream happy path: SSE token+done events, answer grounded via fakes;
  conversation row persisted with `widget_id` set and `user_id` NULL.
- Origin allowlist: disallowed parent_origin → 403; allowed → 200.
- Rate limit: exceeding per-widget limit → 429 (fake limiter).
- **Anonymous-ctx tag-gating test**: org with an access-controlling tag on a
  document → widget visitor gets no hits from that document; untagged documents
  still retrievable (pins the `resolve_user_granted_tags(user_id=None)`
  behavior).
- Migration 0019 applies via the normal Testcontainers chain run.

Frontend: `EmbedChatPage.test.tsx` (config fetch, unavailable state, submit +
streamed answer via mocked service), `EmbedWidgetsPage.test.tsx` (create, list,
revoke-confirm-dialog, snippet copy).

Verification bar (the project standard): full backend suite green (baseline
252 passed / 2 skipped before this feature), frontend suite green (baseline 138),
`ruff check` + `ruff format --check` clean, `tsc -b` clean (delete stale
`*.tsbuildinfo` first), `vite build` clean. Migration applied to the dev Postgres
too (`alembic upgrade head` — the dev DB is separate from Testcontainers; start
`docker compose up -d` first).

Live verification (claude-in-chrome): create a widget in the admin page, open a
local scratch HTML page containing the snippet (serve via a throwaway local
server so it has a real origin), confirm bubble → iframe → real streamed cited
answer; confirm a revoked widget goes dead on the next request; confirm an
origin not on the allowlist is rejected. Remember the dev-env gotchas: real
entrypoints are `backend/main.py` / `backend/worker.py` (run from `backend/`
with `backend/.venv/Scripts/python.exe`), `STORAGE_MODE=local` +
`OPENAI_API_KEY`/`OPENAI_BASE_URL` (OpenRouter values) as process env, restart
stale uvicorn on port 8010 after backend edits, and `vite.config.ts` proxies to
`127.0.0.1:8010`.

## 9. Hard-rule checklist for review (`/review`)

- Cross-domain calls only via services (`knowledge_service`, `chat_service`) —
  never another domain's repository/tables.
- SQL only inside `# ---- repository ----`; routes wire paths; controllers thin.
- Every query org-scoped (public path derives org_id from the URL, then
  everything runs under `tenant_session(org_id)` + `_scoped()`).
- New tenant table ships its own RLS block (0016 pattern, NULLIF cast).
- Package-layout convention: `services/embed.py` starts FLAT; promote to a
  subpackage only if it crosses >200 lines AND 2+ independent responsibilities.
- No LLM/embedder calls outside the existing seams (embed only delegates to
  `chat_service`).
- Next migration after this feature: **0020**.

## 10. Known risks / accepted caveats

- Claimed parent origin is spoofable outside a browser — accepted; rate limit +
  revocation are the backstops (industry-standard posture).
- No CAPTCHA / spend cap in MVP — revisit if a widget sees real abuse.
- `stream_ask` gains an optional `widget_id=None` kwarg — keep the default path
  byte-identical for the existing authenticated chat.
- Anonymous visitors see all NON-tag-gated notebook documents; if an org wants a
  widget notebook partially hidden, they must use access-controlling tags (which
  the anonymous ctx cannot hold) — document this in the admin UI copy if needed.
