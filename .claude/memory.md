# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

---

## F42 Admin debug bundle (2026-07-13, this session — UNCOMMITTED)

Built via the full `/architect` → implement → `/review` loop, in buildplan order (Phase 4's
last item). New `message_traces` table (migration `0014`, schema exactly as designed in
architecture.md) persists, per assistant message, the retrieved hits+distances, the exact
prompt sent to the LLM, and the raw model output — written in the same transaction as the
conversation+message pair (`ChatService._persist`), for **both** `/chat/ask` and
`/chat/stream` (they share `_persist`). New admin-gated `GET /chat/messages/{message_id}/
trace` (`require_admin`), `MessageTraceNotFound` → 404. Frontend: `chatApi.getTrace`, an
inline "Debug" toggle on assistant chat bubbles visible only to owner/admin
(`useAuth().user?.role`), showing hits/final_prompt/raw_output — decided during planning
over a separate debug page, to keep the trace next to the answer it explains.

**Mid-review structural finding, fixed same session (not deferred):** the `/review` pass
caught that `services/chat.py` had grown to 435 lines with 3 repository classes
(Conversation/Message/MessageTrace) plus a new read-only responsibility (`get_trace`)
alongside the original write-path generation pipeline — objectively past the
package-layout convention's promotion trigger (>200 lines AND 2+ independent groups,
"different tables" being the convention's own example). Split into `services/chat/`
(`repository.py`: the 3 repo classes, mirroring `services/ingestion/repository.py`'s
exact precedent; `service.py`: exceptions, pure functions, `ChatService`; `__init__.py`
re-exports only the 4 names an external call site actually uses today —
`chat_service`/`GenerationFailed`/`MessageTraceNotFound`/`build_messages`, per the
locked convention's "re-export what's actually used, don't pad" rule). Zero logic
change — full suite re-ran green before and after. **This is now the reference example
for "repository vs. service split with no independent-subdomain fan-out"** — contrast
with `services/documents/` (split by table into peer subdomains, each independently
callable) and `services/ingestion/` (split by pipeline stage) — chat only ever needed
the repository/service axis since `ask`/`stream_ask` aren't independent stages, they're
one flow with two transports.

**Second review finding, also fixed**: `ChatPanel.tsx`'s `TraceDetails` claimed to cache
fetched traces "per message," but its `useState` lived inside a component that unmounts
on toggle-close (conditional render), so every reopen re-fetched. Fixed with a
module-level `Map<string, MessageTrace>` cache read before the fetch — genuinely no
refetch on reopen now. Note for future test-writing in `ChatPanel.test.tsx`: this cache
is module-scoped and persists across tests in the same file: don't reuse a `message_id`
across two admin+Debug-click tests in one file expecting both to call `getTrace`, or the
second will short-circuit on the cache.

**Verification:** 185 backend tests (180 prior + 5 new: trace persisted+correct on
`/ask`, trace persisted+correct on `/stream`, admin-only 403 for a member, 404 for a
random message_id, 404 cross-org), migration `0014` applied cleanly via the real
Testcontainers run. Frontend: 54 tests (52 prior + 2 new: Debug hidden for a member,
Debug shown + fetches for an admin). ruff check/format clean (only the 3 standing
`scripts/inspect_document.py` findings), `tsc -b` clean, `vite build` clean.
**All work UNCOMMITTED** — staged in the working tree, not yet committed.

**This closes Phase 4 entirely** (F40, F4x, F41, F42 all done). Only F60 (enforced RLS,
Phase 6) remains on the buildplan.

---

## Full-codebase Haiku-swarm review + /chat/stream test suite (2026-07-13, this session — COMMITTED `c7d9b50`)

**A direct ask, not a buildplan item**: review the whole codebase with a swarm of Haiku
agents (orchestrator delegates all read/write, cross-checks their claims) and report.
Ran 7 parallel Haiku agents — 5 domain reviewers (auth, documents/access-roles,
ingestion/seams/storage/queue, retrieval/chat/notebooks, frontend), 1 mechanical
cross-cutting grep sweep (SQL placement, org_id scoping, seam boundary, stale dotted
paths, migration chain 0001→0013, import hygiene, route layering), 1 test runner.

**Verdict: codebase healthy.** Every hard rule passed in every slice, confirmed
independently by both the per-domain reviewers and the mechanical sweep. Test runner
confirmed the full baseline green at session start (173/173 backend + 1 skip, 52/52
frontend, ruff/tsc/build clean, app imports).

**Only substantive finding — closed this same session**: `POST /chat/stream` had ZERO
backend tests (vs 10+ for `/chat/ask`). A Haiku agent wrote 7 tests + a
`_parse_sse_events` helper, appended to `tests/test_chat.py` (email prefix
`chatstream-`): token→done event sequence, persistence parity with `/ask`,
empty-notebook refusal, mid-stream LLM failure → error event (HTTP stays 200), missing
notebook → error event, citation provenance round-trip via the done event, cross-org
isolation. Zero production code changed; no bugs surfaced in `stream_ask`.
Independently re-verified by a second agent (git diff scope, re-run, ruff) before
commit. **New baseline: 180 passed, 1 skipped.** Committed `c7d9b50`.

**Minor findings deliberately NOT fixed (recorded so they aren't re-discovered):**
- Dropped-citation logging (`resolve_citations`) logs counts but not which `[n]`
  markers were dropped — small debuggability improvement if wanted.
- `_InMemoryObjectStore` fakes in `tests/test_ingestion.py` and
  `tests/test_ingestion_dispatch.py` lack the `delete()` method the ObjectStore
  protocol now has (tests never call it; one-liner consistency fix).
- Broad `IntegrityError` catches in `services/documents/folders.py` /
  `services/access_roles.py` assume the name-conflict constraint (low risk, known).
- `_inherited_folder_tags` relies on `list_folders()`'s parent-before-child ordering —
  a comment-only contract (test coverage would catch a regression).
- `tests/test_chat.py:17` / `tests/test_seams.py:16` use `from app.config import
  settings` — works fine via the package re-export (a sweep agent flagged it as a
  violation; orchestrator re-graded it a style nit — the documented shadowing gotcha
  only bites module-ALIAS imports, not object imports).

**Design note re-confirmed intentional (not a defect)**: `/chat/stream` emits a
`{"type":"error"}` SSE event instead of a 404 for a missing/cross-org notebook —
headers are already sent when the generator runs; inherent SSE constraint.

**Swarm-orchestration lesson**: Haiku reviewers over-grade severity (a missing test
suite was reported "CRITICAL", an inherent SSE constraint "MAJOR") and can misapply
documented gotchas (the import-hygiene false positive above) — always re-grade their
findings against the project record before reporting.

---

## Auth hardening: member removal, session revocation, self password-change, login lockout (2026-07-13, earlier session — COMMITTED `a13d307`)

**Built out of buildplan order**, a direct ask ("go through the login/signup/org/member
system, verify against internet best practices, implement fixes") rather than an
F-numbered item. Full design record went through `/architect` (decisions confirmed by
the user, including two explicit trade-off choices — see below). Research this session
found: no member-removal endpoint existed at all (`routes/auth.py` only had
invite/list/change_role), no revocation mechanism, no self-service password change, and
no login rate-limiting — all real gaps against current OWASP Authentication Cheat Sheet
guidance and multi-tenant SaaS RBAC practice. **Explicitly scoped OUT this round** (user
picked "harden auth, skip email" over "everything including email invites"): tokenized
email-invite links, password-reset-via-email, MFA — all would require adding a new email
provider seam, a bigger architectural addition not undertaken here.

**What was built (migration `0013`, adds 4 columns to `users`):**
1. **`is_active` (member removal)** — soft-delete, not a hard `DELETE`: FK check done
   first (`chat.messages`/`knowledge_bases` are `ON DELETE SET NULL`, `user_access_roles`
   is `CASCADE`) confirmed hard-delete would strip authorship from history, so removal is
   reversible instead. New `PATCH /auth/users/{id}/status` (`{"is_active": bool}`),
   mirrors `change_role`'s exact guard shape (admin/owner only, can't target self, can't
   target an owner) in a new `AuthService.set_member_active`.
2. **Instant revocation, no token blacklist needed** — the real finding that shaped the
   whole design: `current_user` (`middleware/deps.py`) already does a full DB read on
   *every* authenticated request, not just on refresh. Adding `is_active`/`token_version`
   checks there (and in `AuthService.refresh`) makes deactivation take effect on the
   member's very next API call, and makes a password change invalidate every other
   session — zero new infrastructure (no Redis denylist, no sessions table).
3. **`token_version` (session revocation on password change)** — bumped by
   `UserRepository.update_password`, embedded in both access+refresh JWTs as a `"tv"`
   claim (`issue_access_token`/`issue_refresh_token` now require `token_version=...`),
   checked against the DB value on every `current_user` and `refresh()` call. New
   self-service `POST /auth/me/password` (`ChangePasswordRequest`: current+new password,
   argon2-reverifies the current one) returns a **fresh token pair** so the session making
   the change keeps working while every other session dies instantly. User explicitly
   confirmed this over the simpler "just change the hash" option.
4. **Per-account login lockout** — `LOGIN_LOCKOUT_THRESHOLD=5` / `LOGIN_LOCKOUT_MINUTES=15`
   (`utils/constants.py`), counted on the `users` row itself, never by source IP (OWASP:
   IP-scoped lockout lets an attacker DoS a victim by spoofing addresses). New
   `AccountLocked` exception → HTTP 423 with an **explicit** "Account temporarily locked,
   try again in N minute(s)" message — user chose this over a fully generic message,
   reasoning that this app already reveals org-membership during multi-org login
   disambiguation (`AmbiguousLogin`), so a stricter anti-enumeration bar wasn't consistent
   with existing behavior anyway. Counter resets on success; if `locked_until` has already
   passed when a new attempt arrives, the window resets fresh rather than extending
   indefinitely under sustained attack. `find_login_candidates` now filters
   `is_active=True` in the query itself, so a deactivated member's login attempt gets the
   exact same generic "Invalid email or password" as a wrong password — deliberately a
   DIFFERENT message than the lockout case (admin-initiated status change on someone
   else's account vs. the user's own repeated attempts).
5. **A real transaction-ordering bug caught and fixed during implementation, not by
   review**: the first draft of `login()`'s failed-attempt counter raised
   `InvalidCredentials` from inside the `async with session.begin()` block on a wrong
   password — since `session.begin()` rolls back on any exception escaping the block,
   this would have silently discarded the very failure-count increment it just flushed,
   making the lockout counter never actually increment. Fixed with a `pending_error`
   local-variable pattern: write the counter update, capture the exception to raise, let
   the block exit normally (commit), THEN raise. All other raise sites in `login()`
   (no candidates, ambiguous, wrong org_id, already-locked) happen before any write, so
   they're unaffected — only the wrong-password branch needed this treatment.
6. **Frontend** (`UsersPage.tsx`): "Remove"/"Reactivate" button per row (confirm-guarded
   like the existing document-delete pattern), reuses the exact same `canEdit` condition
   as role-change (self/owner both blocked) gated additionally on `isAdmin`; inactive rows
   render at `opacity-50` with a "Removed" badge; role `<select>` hidden while inactive
   (reactivate first, then re-assign role — avoids a meaningless intermediate state). New
   self-service "Change your password" collapsible form, visible to every role (not just
   admin) — critically persists the fresh access token returned by the endpoint via
   `setStoredAccessToken` immediately, since the token used to make the request is now
   stale the instant the call succeeds.

**Verification:** 173/173 backend tests (161 prior + 12 new: lockout threshold/reset/
window-expiry, deactivate blocks next request AND relogin, deactivate guard rules (self/
owner/non-admin), reactivate restores login, password-change wrong-current-password
rejected, password-change invalidates the old token while the returned new one still
works), ruff check clean (only the 3 pre-existing `scripts/inspect_document.py`
findings), ruff format clean. Frontend: 52/52 vitest (44 prior + 8 new), `tsc -b` clean
(had to add `is_active` to two other test files' inline `User` fixtures —
`FolderTree.test.tsx`, `AccessRolesPage.test.tsx` — since the type is now required),
`vite build` clean. Migration `0013` applied cleanly against a real Testcontainers
Postgres container as part of the full pytest run (not just unit-tested in isolation).

**Gotcha reconfirmed this session**: a background (non-blocking) pytest run against the
SAME already-warm Testcontainers container returned `43 passed, 131 skipped` (25 minutes
runtime) — looked like a mass regression at first glance. Immediately re-ran synchronously
against the same warm container: clean `173 passed, 1 skipped` in 38s. This is the
existing documented Windows/Testcontainers/Ryuk flakiness gotcha, not a real failure —
**don't trust a single degraded-skip pytest run on this project without a synchronous
re-run to confirm**, especially one issued via a background/detached process.

**Committed `a13d307`.**

**Next migration is now `0014`.**

---

## Access Roles (tag-based RBAC) + folder tree drag-and-drop (2026-07-12, same session — SUPERSEDES the folder-restriction feature immediately below)

**Full design record:** `docs/access-roles-dnd-plan.md` (produced via `/architect`,
confirmed by the user before implementation). **This fully replaces the
`folders.restricted` boolean feature from earlier the same session** (see the section
right below this one) — that column, its endpoint, its inheritance-walk code, and its
FolderTree lock/unlock button are ALL DELETED, not deprecated. The user asked for this
replacement directly: custom roles instead of a binary owner/admin-vs-member flag, plus
a friendlier folder tree (drag-and-drop instead of a `<select>` dropdown).

**What was built:**
1. **Access Role** — a new concept, deliberately separate from the system role
   (owner/admin/member, unchanged, still governs invites/role-changes). New tables
   (migration `0012`): `access_roles`, `user_access_roles`, `access_role_tags`, plus
   `folder_tags` (folders can now carry tags, mirroring the pre-existing
   `document_tags`). A tag becomes **"access-controlling"** the instant it's granted to
   any Access Role — an untagged resource, or one tagged only with tags no role has
   ever been granted, stays open to every org member (zero behavior change for the
   common case). New domain `app/{models,services,routes,controllers}/access_roles.py`
   (routes/controllers), all admin/owner-gated.
2. **`resolve_allowed_documents` rewrite** (`app/services/retrieval.py`): owner/admin
   bypass unchanged; for everyone else, one top-down pass over `list_folders()`
   computes each folder's inherited effective tag set (`_inherited_folder_tags` — same
   shape as the old `_inherited_restricted_ids` but accumulating tag-id sets instead of
   a boolean), a document's own direct tags add to whatever its folder chain inherits,
   and a document is visible unless its effective tags intersect the org's
   access-controlling tags AND that intersection misses every tag the requesting
   user's Access Roles grant.
3. **Folder tagging is admin/owner-only; document tagging stays open to any member**
   (unchanged from before) — a deliberate asymmetry, since a folder tag cascades to an
   entire subtree. **Named, accepted risk**: because document-tagging has no
   permission gate, a plain member CAN accidentally make a document invisible to most
   of the org by attaching a tag that happens to already be granted to some Access
   Role — a direct, foreseeable consequence of "reuse the same tags for both
   organization and access control" + "keep document-tagging permission-free," both
   confirmed by the user.
4. **New `PATCH /documents/{id}/folder`** (`move_document`) — didn't exist before;
   required for the drag-and-drop-a-document-onto-a-folder UX to work at all.
5. **Frontend**: `FolderTree.tsx` lost the lock/unlock button entirely, gained
   always-visible tag badges (click-to-untag for admin/owner) + an admin-only "+ tag"
   grant select, and native HTML5 drag-and-drop (`draggable`/`onDragStart`/`onDragOver`/
   `onDrop`) replacing the `<select>`-based move dropdown — chosen over a tree library
   like `react-complex-tree` specifically to add zero new dependency (this codebase has
   none today beyond react-query/react-router). `DocumentList.tsx` rows are now drag
   sources too (dropping one onto a FolderTree folder calls the new move-document
   endpoint). New `pages/AccessRolesPage.tsx` (create role, grant/revoke tags,
   assign/remove members) plus a **new tag-creation control on that same page** — a
   real gap caught only during manual browser testing: there was NO tag-creation UI
   anywhere in the app before this (only document/folder tag *attach*, never *create*).
   New `AccessRolesPage` nav item, admin-gated like the existing Users page.

**Verification:**
- Backend: 158/158 tests (full suite, including new `tests/test_access_roles.py` CRUD/
  grant/assign/isolation coverage and rewritten tag-based cases in
  `tests/test_retrieval.py`), ruff/format clean (3 pre-existing findings only),
  migration `0012` applied cleanly to both a fresh Testcontainers DB and the running
  dev Postgres.
- Frontend: 44/44 vitest (including drag-and-drop simulated via a Map-backed fake
  `DataTransfer` passed through `fireEvent.dragStart`/`fireEvent.drop`), `tsc -b` +
  `vite build` clean.
- **Live browser verification (Chrome via claude-in-chrome), the real end-to-end
  proof**: created two folders, dragged one onto the other to reparent it (confirmed
  via API: `parent_id`/`path` correctly rebuilt), dragged it back to root by dropping
  on "All documents"; created a tag and an Access Role on the new page, granted the tag
  to the role, assigned an invited member to it; tagged the Finance folder with that
  tag in FolderTree (badge appeared live); uploaded+ingested a real document into that
  folder, attached it to a notebook, and confirmed via `/retrieval/search` that an
  **outsider member (no matching role) got zero results** while the **role-holding
  member got the grounded hit** — the tag-based gate genuinely works, not just at the
  unit-test level.
- **One real bug found and fixed during this same verification, unrelated to the new
  feature**: a stale `uvicorn` process (started earlier in the session, before this
  feature's model/migration edits) was still running old code and threw
  `UndefinedColumnError: column folders.restricted does not exist` on folder-create —
  looked like a live regression until traced to the process simply needing a restart
  to pick up the code changes. **Lesson for next session**: after editing backend
  models/migrations mid-session, restart any already-running dev `uvicorn`/`arq`
  processes before trusting a "live" browser check — they don't hot-reload.

**Gotcha**: `fireEvent.dragStart`/`fireEvent.drop` in vitest+jsdom need a manually
constructed `{ setData, getData }` object passed as `{ dataTransfer }` in the event init
— jsdom's real `DataTransfer` doesn't implement storage. Reuse the *same* object
instance across the paired dragstart/drop calls to simulate the OS-level handoff.

**Next migration is now `0013`.**

---

## Document hard-delete + folder-based access restriction (2026-07-12, this session — SUPERSEDED, see section above)

> **STALE as of later the same session**: `folders.restricted`, its endpoint, and its
> FolderTree UI (described below) were fully removed and replaced by the Access Roles
> system in the section above, per a direct follow-up ask. The document hard-delete
> half of this entry is still current and unaffected.

**Built out of numeric buildplan order** (no F-number — a direct ask, not a
`buildplan.md` line item), on top of the committed single-MVC layout. Full design
record: `docs/document-delete-folder-restriction-plan.md` (produced via `/architect`,
confirmed by the user before implementation). Session also ran a full health check
first: 135/135 backend tests + 30/30 frontend tests green, ruff/tsc/build clean, and a
live smoke test (signup→upload→auto-ingest→notebook→chat) confirmed the whole golden
path already worked before any new code was written.

**Two features, both closing gaps an Explore-agent audit found were previously
real (not assumed): there was NO document-delete endpoint at all, and
`resolve_allowed_documents` was a literal all-org-docs stub.**

1. **Hard document delete** — `DELETE /documents/{id}`. Deletes the `Document` row
   inside a transaction (cascades to sections/chunks/embeddings/document_tags/
   knowledge_base_documents — all were ALREADY `ON DELETE CASCADE`, so no other
   repository needed touching), then deletes the object-store blob(s) AFTER commit
   (source file + parse artifact JSON, both known keys from `document.storage_key` /
   `document.metadata_["parse_artifact_key"]`). Order matters: DB-first means a failed
   blob delete only orphans a blob (already an accepted future "orphan sweep" gap),
   never leaves a document row pointing at nothing.
2. **Folder-based access restriction** — new `folders.restricted` boolean (migration
   `0011`, default `false`). Owner/admin always bypass (`ADMIN_ROLES`); a `member` is
   denied any document in a restricted folder OR ITS SUBTREE (inherited downward,
   computed live in `resolve_allowed_documents` via one top-down pass over
   `list_folders()`'s already-parent-before-child-ordered result — no caching, so
   toggling `restricted` takes effect on the very next request, satisfying the "sorted
   as soon as tags/folders change" ask without needing any resync job). New admin-only
   `PATCH /documents/folders/{id}/restriction`.

**Explicit scope boundary (a deliberate choice, not an oversight):** restriction only
gates chat/retrieval (`resolve_allowed_documents`), NOT the plain browsing endpoints
(`GET /documents`, `GET /documents/folders`) — those still show everyone everything, by
design, because `documents_service.list_documents`/`get_document` are also called
internally by ingestion/knowledge services with non-interactive worker contexts
(`role=None`), and folding restriction into those shared accessors risked silently
breaking the pipeline. If browsing should ALSO be gated later, that's a separate,
slightly riskier follow-up — named, not built.

**Locked-decision amendment:** `ObjectStore` gained a `delete(key)` method (S3
`delete_object` / local `Path.unlink(missing_ok=True)`, both idempotent on a missing
key) — this supersedes the prior "put+get only" decision recorded pre-2026-07-12; that
decision itself named this exact feature as the future trigger for the change.

**Frontend wired too** (confirmed with the user before building — backend-only was the
other option): `DocumentList.tsx` gained a hover-reveal delete button (×) with a
`window.confirm` guard; `FolderTree.tsx` gained an always-visible red "restricted" badge
(visible to everyone) plus a hover-reveal lock/unlock toggle button gated by
`useAuth().user?.role` (owner/admin only) — first component test to need `useAuth`,
so `FolderTree.test.tsx` now mocks `../hooks/useAuth` (pattern: `vi.mock(...)` +
a `mockUser(role)` helper reset in `beforeEach`). Added `title={node.name}` tooltip to
the folder-name button since the restricted badge eats into the already-narrow sidebar
name space.

**Verification:** 141/141 backend tests (135 + 6 new: 3 delete, 1 cascade-cleanup via
direct Section/Chunk/Embedding queries, 1 cross-org 404, 1 missing-404), 35/35 frontend
tests (30 + 5 new), ruff/tsc/build all clean. Migration `0011` applied cleanly to both
a fresh Testcontainers DB (via the suite) and the running dev Postgres. **Live end-to-end
proof, not just unit tests**: real HTTP session with an owner + an invited `member` user
— member blocked from a restricted folder's docs in `/retrieval/search`, owner
unaffected, member gets 403 trying to toggle the flag, and un-restricting live-unblocks
the member's very next search with no resync step. Hard delete verified live too:
DELETE removes the DB row (confirmed via list + double-delete 404) AND the local-disk
blob file (confirmed via filesystem check — only empty leftover directories remained,
zero file content). Also verified in an actual browser (Chrome via claude-in-chrome):
signed up, created a folder, clicked the lock toggle (badge + button rendered and
worked), uploaded a doc via API + refreshed, clicked delete, confirmed via the native
dialog — document disappeared from the list.

**Gotcha for next session:** clicking a button wired to `window.confirm`/`window.alert`
through browser automation blocks the tab (CDP screenshot/exec calls hang until the
native dialog is dismissed) — recovered by sending a bare `key: Return` press (accepts
the dialog) rather than trying to click through it. Don't `left_click` a confirm-guarded
delete button in automated browser testing; drive it via `key` press once the dialog is
already open, or avoid clicking it directly and verify via API + a page refresh instead.

**Next migration is now `0012`** (F42's `message_traces` claim on `0011` is superseded —
this feature took `0011` instead, since it landed first).

---

## Single-MVC re-refactor (2026-07-02 — COMMITTED as `81bd90f`, confirmed 2026-07-12)

**Pure structural re-refactor — zero logic/schema/API change.** Yesterday's layer-first MVC
(2026-07-01, backend ONLY) was itself re-refactored into a single unified MVC: Express-style
backend (JSON-only, no view layer) + conventional React SPA frontend as the unified view
layer. **Every path reference in older sections below describing 2026-07-01 layer-first
layouts is now STALE** — this table and the old→new mappings below are the new
authoritative record. **Correction (2026-07-12): every "work is UNCOMMITTED" claim below
in this section was stale by the time the 2026-07-12 session started — `git log` showed
this refactor already committed as `81bd90f` ("refactor: single-MVC restructure..."),
one commit ahead of the `ab3e0f3`/`6ff4be7` layer-first-MVC commits. Some other session
between 2026-07-02 and 2026-07-12 committed it without updating this file. Trust `git log`
over this file's commit-status claims going forward.**

**Owner decisions:** (a) literal `routes/` + `controllers/` split on backend (express-style);
(b) full collapse of `repositories/` and `exceptions/` into `services/`, and `schemas/`
into `models/` (accepting warned trade-off of less separation); (c) frontend types in
`src/types/`.

### Backend old→new mapping (layer-first MVC 2026-07-01 → single-MVC 2026-07-02)

| Old (layer-first) | New (Express-style, single MVC) |
|---|---|
| `app/models/<d>.py` + `app/schemas/<d>.py` | `app/models/<d>.py` (merged, "# ---- API schemas ----" section marker) |
| `app/controllers/<d>.py` | `app/routes/<d>.py` (APIRouter wiring, decorator-call form `router.post(path, **kwargs)(handler)`) + `app/controllers/<d>.py` (plain handler functions) |
| `app/repositories/<d>.py` (or `repositories/<d>/`) | merged into `app/services/<d>.py` ("# ---- exceptions ----"/"# ---- repository ----"/"# ---- service ----" sections) |
| `app/exceptions/<d>.py` | merged into `app/services/<d>.py` likewise |
| `app/tasks/<d>.py` | `app/services/<d>/tasks.py` (under the domain service subpackage) |
| `app/controllers/deps.py` | `app/middleware/deps.py` |
| `app/platform/context.py` | `app/middleware/context.py` |
| `app/platform/{config,db,logging}.py` | `app/config/{settings,db,logging}.py` |
| `app/platform/{tokens,passwords,constants,http}.py` | `app/utils/` |
| `app/platform/repository.py` (BaseRepository) | `app/services/base.py` |
| `app/platform/seams/` | `app/services/seams/` |
| `app/platform/{storage,queue}.py` | `app/services/` |
| `app/models/identity.py` | `app/models/auth.py` (renamed 2026-07-02 to match the auth domain naming used by routes/controllers/services) |
| `app/models/retrieval.py` (was schemas only) | `app/models/retrieval.py` (new; schemas only) |
| DELETED entire top-level dirs | `app/platform/`, `app/schemas/`, `app/repositories/`, `app/exceptions/`, `app/tasks/` |

### Frontend old→new mapping (2026-07-01 layer-first views → 2026-07-02 conventional SPA)

| Old (2026-07-01) | New (2026-07-02) |
|---|---|
| `src/models/` (types split from lib/api.ts) | `src/types/` |
| `src/controllers/<x>Controller.ts` (api namespaces split from lib/api.ts) | `src/services/<x>Service.ts` (exported symbol names unchanged: authApi, etc.) |
| `src/lib/api.ts` | `src/services/http.ts` |
| `src/lib/auth.tsx` | `src/context/AuthContext.tsx` + extracted `src/hooks/useAuth.ts` |
| `src/views/app/{AppShell,Sidebar}.tsx` | `src/layouts/` |
| routed screens (HomePage, LoginPage, SignupPage, DocumentsPage, NotebookPage, UsersPage) | `src/pages/` |
| component tree (ChatPanel, CitationPanel, DocumentList, FolderTree, ProtectedRoute, StatusBadge) | `src/components/` |
| `index.css` | `src/styles/` |
| DELETED entire dirs | `src/views/`, `src/controllers/`, `src/models/`, `src/lib/` |

### Verification record

**Verification (this session, agent-driven):** 4 sequential backend slices (models+schemas,
controllers+routes, services+repositories, exceptions+tasks+middleware+config+utils) + 1
frontend slice + fixups. Each slice individually green; full codebase state at end:
- **Route table proven byte-identical to pre-refactor HEAD** — OpenAPI paths+methods
  diffed against a temporary git worktree of HEAD (commit `6ff4be7`).
- **Backend offline suite at baseline:** 42 passed / 93 skipped (Docker was DOWN during
  the refactor phase). After `pip install openai` (v2.44.0) in `backend/.venv` and a
  follow-up Docker-gated full-suite re-run: **135 passed, 1 skipped, 0 failures** — exact
  match to the 2026-07-01 MVC refactor baseline. The 1 skip is the opt-in `real_parser`
  test needing a live `OPENROUTER_API_KEY` (unrelated to this refactor). Confirms the
  refactor is a true zero-logic-change: every previously-skipped DB-backed test now passes
  unchanged.
- **3 refactor-fallout bugs in TEST FILES ONLY (all fixed during Docker-gated re-run):**
  a. `tests/conftest.py` + `tests/test_tenant_session.py`: `from app.config import settings as config_mod`
     bound the Settings OBJECT, not the module — `config_mod.settings.X` raised AttributeError on
     all 92 DB-backed tests. Root cause: **`app/config/__init__.py`'s re-export of
     `from app.config.settings import settings` SHADOWS the `app.config.settings` submodule
     as a package attribute** — even `import app.config.settings as X` binds the object
     (import-as resolves via getattr on the package). Fixed by binding directly:
     `from app.config.settings import settings`.
  b. `tests/test_ingestion_dispatch.py`: three monkeypatch STRING literals still said
     `"app.tasks.ingestion.get_object_store"` — dotted-path strings are invisible to import
     sweeps. Updated to `"app.services.ingestion.tasks.get_object_store"`.
  c. `migrations/versions/0002_rls_scaffolding.py`: import WAS updated (app.platform.config →
     app.config.settings) during the refactor — a deliberate exception to "never touch migrations".
     The migration imports settings at runtime; without the edit every fresh-DB migration run would
     crash. Schema operations untouched. Validated by the full suite applying the whole chain on a
     fresh Testcontainers container.
- **`ruff check`:** only the 3 pre-existing `scripts/inspect_document.py` findings; `ruff format` clean.
- **Frontend:** `tsc -b` clean, 30/30 vitest, `vite build` clean.
- **Live end-to-end smoke PASSED** (docker compose up pg+redis; alembic at head 0010; uvicorn boot):
  GET /health 200, POST /auth/signup 201 (real JWT), POST /auth/login → GET /auth/me 200,
  authed GET /documents 200. The new routes/→controllers/ split serves real traffic.
- **All work UNCOMMITTED** in the working tree (staged via `git mv`), on main.

### Gotchas (new or updated for 2026-07-02)

- **`app/config/__init__.py`'s re-export SHADOWS the submodule.** The line
  `from app.config.settings import settings` inside `app/config/__init__.py` re-exports
  the `settings` object. But this ALSO shadows `app.config.settings` (the submodule) as
  a package attribute — `import app.config.settings as X` binds the object, not the module
  (import-as resolves via getattr). Any code that needs the settings singleton must bind
  directly: `from app.config.settings import settings`, never via a module alias.
- **Dotted-path STRING LITERALS in monkeypatch/patch targets are invisible to import sweeps.**
  After any module move, grep for dotted module paths inside string literals
  (e.g., `monkeypatch.setattr("app.tasks.ingestion.get_object_store", ...)`), not just
  import statements.
- **Migrations/versions/*.py are immutable EXCEPT when a migration itself imports a module
  that no longer exists.** If a migration's `def upgrade()` imports from a domain that was
  refactored away (e.g., `from app.platform.config import settings` after `app.platform`
  was deleted), the migration will crash on every fresh-DB run — fix the import in the
  migration file itself. Schema operations and Alembic directives stay untouched.
- **`services/chat.py`:** seam `Message` type imported as `SeamMessage` to avoid colliding
  with the `Message` ORM model now that repo+service+models share the same import space
  (post-collapse).
- **`services/documents/documents.py`:** `FolderRepository` is imported function-locally inside
  `upload_document()` to avoid a documents↔folders circular import (local import defers the
  cycle until inside the function body).
- **`register_exception_handlers` was removed from `app/utils/__init__.py` re-exports** —
  an import-time cycle emerges if it's re-exported; `main.py` imports it directly from
  `app.utils.http` instead.
- **Stale `*.tsbuildinfo` can make `npx tsc -b` a false no-op** — delete before trusting it
  (this bit us during frontend restructure; a prior `.tsbuildinfo` file lied about what was
  built).
- **`EMBED_DIM` in `models/ingestion.py`** imports from `app.services.seams.protocols` (was
  hardcoded temporarily during refactor, restored after).

---

## MVC refactor cross-verification + commit (2026-07-01, this session — SUPERSEDED 2026-07-02)

**The MVC refactor is now COMMITTED (`6ff4be7`) and independently re-verified.** Before
committing, ran a swarm of 7 Haiku agents (Opus as orchestrator; all reads/writes done by
agents) auditing every slice against `docs/mvc-refactor-prompt.md`: models/schemas,
controllers/platform/entrypoints, services/repositories + both boundary rules,
exceptions/tasks/migrations/scripts, a repo-wide stale-import sweep, the frontend
model/controller/view split, and a docs self-audit. **Codebase verdict: fully correct
layer-first MVC — zero code issues.** Confirmed: no SQL outside repositories, cross-domain
calls via services only, the ingestion→documents one-way dep + controller-composed pipeline
circular-guard intact, universal `org_id` scoping via `BaseRepository._scoped()`, all 13
tables register, migration history untouched, no code-breaking stale references anywhere,
all old domain dirs + `src/features/` deleted.

- **Two doc-staleness fixes made** (only findings): (1) added a historical-path disclaimer
  block at the top of `progresstracker.md` (it lacked memory.md's disclaimer despite stale
  domain-first paths in its completed-feature entries) pointing to the old→new mapping table
  here; (2) corrected `architecture.md` `_parse_markdown_outline` reference from `seams.py`
  to `seams/real_parser.py` (seams is now a package). All other docs (codestandards,
  librarydocs, orchestrator, root CLAUDE.md, the refactor prompt itself) were already clean.
- **Green at commit time:** 135 backend tests vs real Testcontainers pgvector (1 real_parser
  deselected), 30/30 frontend vitest, `tsc -b`/`vite build` clean, ruff check clean except
  the 3 standing `scripts/inspect_document.py` findings, ruff format clean.
- **Gitignore hardened:** `.localstorage/` (LocalDiskObjectStore dev blobs), `.playwright-mcp/`,
  and `/pdf/` (5MB copyrighted `kech104.pdf` — manual-validation asset, never a fixture) are
  now ignored, NOT committed. `docs/` (mvc-refactor-prompt.md + er-diagram.md) IS committed as
  the in-repo historical record. Commit is on `main` (consistent with the whole project history).

---

## MVC layout refactor (2026-07-01, this session — SUPERSEDED by 2026-07-02 single-MVC re-refactor)

**HISTORICAL RECORD.** This was a pure structural refactor (zero logic/schema/API change)
from domain-first to layer-first MVC, committed 2026-07-01 as `6ff4be7`. Backend went from
`app/<domain>/` to `app/{models,schemas,controllers,services,repositories,exceptions,tasks}/`.
Frontend went from `src/features/` to `src/{models,controllers,views}/`. **This layout was
itself re-refactored 2026-07-02 into a single-MVC backend (routes+controllers, services
collapsed) + conventional SPA frontend (types, services, pages, components).** Every
2026-07-01 path below in this file is STALE. The authoritative old→new mapping (domain-first
→ layer-first → single-MVC) lives in the new "Single-MVC re-refactor" section at the top of
this file. Full 2026-07-01 spec: `docs/mvc-refactor-prompt.md` §3 (kept in repo as
historical spec). Old domain dirs and `src/features/` are fully deleted.

| Old (domain-first) | New (layer-first) |
|---|---|
| `app/<domain>/models.py` | `app/models/<domain>.py` |
| `app/<domain>/schemas.py` | `app/schemas/<domain>.py` |
| `app/<domain>/router.py` | `app/controllers/<domain>.py` (knowledge→`notebooks.py`) |
| `app/<domain>/service.py` (or `service/`) | `app/services/<domain>.py` (or `services/<domain>/`) |
| `app/<domain>/repository.py` (or `repository/`) | `app/repositories/<domain>.py` (or `repositories/<domain>/`) |
| `app/<domain>/exceptions.py` | `app/exceptions/<domain>.py` |
| `app/identity/{tokens,passwords,constants}.py` | `app/platform/{tokens,passwords,constants}.py` |
| `app/identity/deps.py` | `app/controllers/deps.py` |
| `app/ingestion/tasks.py` | `app/tasks/ingestion.py` |
| `app/documents/status.py` (`DocumentStatus`) | merged into `app/models/documents.py` |
| `src/features/<area>/*` | `src/views/<area>/*` |
| types in `src/lib/api.ts` | `src/models/{auth,documents,knowledge,chat}.ts` |
| api namespaces in `src/lib/api.ts` | `src/controllers/{auth,documents,notebooks,chat}Controller.ts` |

- `documents/service`, `documents/repository`, `ingestion/service` KEPT their
  subpackage shape (just moved: `services/documents/`, `repositories/documents/`,
  `services/ingestion/`) — package-layout convention untouched, only the parent
  namespace changed.
- Each new top-level package (`models/`, `schemas/`, `controllers/`, `services/`,
  `repositories/`, `exceptions/`, `tasks/`) got an `__init__.py` that re-exports
  everything from that layer — cross-domain imports still work via either the
  specific submodule or the package root.
- `main.py`/`worker.py`/`migrations/env.py` import lists updated to the new paths;
  `migrations/versions/*.py` untouched (immutable history).
- All fake/offline tests, `ruff check`/`ruff format --check` (only the 3 pre-existing
  `scripts/inspect_document.py` findings remain), every new-path import smoke test,
  `Base.metadata` table registration (13/13 tables), and `GET /health` all passed.
  Frontend: 30/30 vitest, `tsc -b` clean, `vite build` clean.
- **Docker-gated verification CLOSED (2026-07-01, same session, follow-up):** initial
  pass had Docker Desktop refuse to launch in-sandbox (93 Testcontainers-Postgres tests
  skipped, `pytest`: 43 passed / 93 skipped). Docker came up on retry; full suite re-run
  against real Postgres: **135 passed, 1 skipped** (the skip is the opt-in `real_parser`
  test, needs a live `OPENROUTER_API_KEY` — unrelated to Docker). Confirms the refactor
  is a true zero-logic-change: every previously-skipped DB-backed test now passes
  unchanged. `ruff check .` re-confirmed still exactly the 3 pre-existing findings.

---

## Real-embedder retrieval validation (2026-07-01, this session)

**Ask:** verify embeddings + retrieval work correctly with the real API key now in
`.env`, then exercise retrieval across documents with different section-hierarchy
shapes and fix the codebase if anything was wrong. **Result: retrieval/grounding is
fully correct; nothing in the codebase needed fixing.** One real, load-bearing finding
about heading recovery — corrected a stale claim in `progresstracker.md`'s F23 entry.

- **Real key wiring note:** `.env` only had `OPENROUTER_API_KEY` set. `RealEmbedder`/
  `RealLLM` (`app/platform/seams/real_llm.py`) read `OPENAI_API_KEY`/`OPENAI_BASE_URL`
  — a SEPARATE config pair from `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL` (used only by
  `RealParser`). Ran with `OPENAI_API_KEY=<the OpenRouter key>` and
  `OPENAI_BASE_URL=https://openrouter.ai/api/v1` set as process env (not written to
  `.env`) — same one-key-feeds-all-3-seams pattern as F40's manual gate, just made
  explicit here since it silently FAILED (`FAILED`/`EMBEDDING`, "OPENAI_API_KEY is not
  set") the first attempt.
- **Test method:** in-process `httpx` + `ASGITransport` against `main.app` (same pattern
  as `tests/test_real_parser_integration.py`), `FakeJobQueue` override to drive ingestion
  stages manually, `STORAGE_MODE=local` (no R2 creds). One notebook, multiple documents,
  real `/documents/upload` → `/ingestion/.../parse|structure|embed` → `/notebooks/{id}/
  documents/{doc_id}` → `/retrieval/search` + `/chat/ask`. Scripts were throwaway
  (scratchpad only, never committed, per the F51 precedent for manual validation
  scripts).
- **Generated 3 synthetic PDFs via reportlab** with deliberately different heading
  shapes (flat/no-headings, shallow 2-level, deep 3-level with a repeated H2 name
  "Diet" under two different H1 parents "Lions"/"Tigers" — to stress path/content
  disambiguation) to test hierarchy variation in a controlled way.
- **FINDING — `cloudflare-ai` (OpenRouter's file-parser plugin) does not recover
  semantic headings for these documents; it only marks page boundaries.** All 3
  synthetic PDFs AND a re-run of the known-good `pdf/kech104.pdf` (36-page real
  textbook, previously recorded in `progresstracker.md`'s F23 entry as "39 sections,
  genuine 3-level tree") produced the IDENTICAL wrapper structure: `document.pdf >
  Metadata > Contents > Page N` (one leaf section per page, `heading='Page N'`)
  regardless of the actual document content or visual heading styling (bold/large
  font). Directly inspected the raw parsing artifact JSON for `kech104.pdf`: the real
  heading text ("4.1 KÖSSEL-LEWIS APPROACH...") IS present in the extracted text but
  fused directly into the surrounding paragraph with ZERO markdown or even whitespace
  separating it (`"...MOLE CULAR S T R U CTURE4.1 K◌SSEL-LEwiS AppROACH tOCHEMiCAL
  BOnDinGIn order to explain..."`) — there is no signal of any kind
  (`#`-markdown/bold/newline) for `_parse_markdown_outline`'s regex to detect. **This is
  a vendor/engine characteristic on two-column academic PDFs, not a parsing bug** — per
  the locked "never fabricate" design (`real_parser.py`), the code correctly does NOT
  invent heading structure that isn't signaled. The prior "genuine 3-level tree"
  description in `progresstracker.md` was corrected — it was technically 3 levels deep
  (root > Metadata/Contents > Page N) but never reflected the document's real
  chapter/subsection structure. **No code change made** — broadening the heading regex
  would not help (there is no alternate signal to detect) and inventing headings via
  NLP heuristics would violate the locked contract; this is a documentation fix, not a
  code fix.
- **Retrieval + grounded generation stayed fully correct despite the coarse (page-level)
  hierarchy** — confirmed this is because F31 retrieval keys off chunk-CONTENT
  embeddings, not section labels/paths, so hierarchy quality has no bearing on
  retrieval correctness at the MVP (flat) stage. Verified across two separate multi-doc
  notebook runs (real API key throughout):
  - 3-doc notebook (flat crocodile fact-sheet, shallow Acme-Corp-handbook-style,
    deep Lions/Tigers guide with the repeated "Diet" H2 name under different parents):
    every targeted question retrieved the correct document and correct fact, correctly
    disambiguating "Diet > Hunting Behavior" (lions) from "Diet > Preferred Prey"
    (tigers) purely via chunk-content embedding similarity — no section-path confusion
    even though both docs' sections collapsed to the same generic wrapper shape.
  - 2-doc notebook (flat crocodile doc + the real 36-page/110-chunk `kech104.pdf`):
    a chemistry question ("Kossel-Lewis approach... octet rule") correctly retrieved
    tight-distance (0.35–0.49) chunks from `kech104.pdf` with a rich, correctly-cited
    `[1][2][3][4]` grounded answer; the crocodile question correctly retrieved the flat
    doc instead (not confused despite both docs sharing a notebook); a follow-up
    chemistry question (octet-rule exceptions) also correctly grounded with citations.
  - Both runs: an out-of-scope question ("2022 FIFA World Cup") correctly triggered the
    exact refusal behavior ("I don't have that in the provided sources.", zero
    citations) even with multiple unrelated documents in the notebook — first time this
    was verified with >1 document present (F40's original gate used a single document).
- **Open question surfaced for future V2 hierarchical retrieval design:** if
  `cloudflare-ai` typically degrades to page-level granularity on real multi-column
  PDFs, V2's planned hierarchical retrieval will often only have "page" as its
  practical section granularity for this vendor/engine, not true chapter/subsection
  structure — worth a deliberate design conversation (try `mistral-ocr` engine instead?
  a different `PARSER_MODEL`? an LLM heading-detection post-pass?) before building V2,
  not something to solve reactively then.

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
| F42 Admin debug bundle | uncommitted | `message_traces` (migration 0014); `GET /chat/messages/{id}/trace` admin-gated; `services/chat/` split into repository.py+service.py |

**Remaining: F60 (RLS) only — Phase 4 is now fully complete.**

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
- **F24 pipeline composed at `app/controllers/documents.py`** (was `documents/router.py` pre-MVC-refactor), not `app.services.documents`, to avoid a circular import (`app.services.ingestion` already imports `app.services.documents`).
- **Deterministic arq `job_id` (`f"ingestion:{stage}:{document_id}"`)** is the REAL concurrency guarantee against double-enqueue under redelivery — a before/after DB status check alone is NOT sufficient (two concurrent deliveries can both read the same "before" status in separate transactions before either writes).
- **F41 citations persist every call as a fresh Conversation + user Message + assistant Message** — no conversation reuse/multi-turn threading until a future feature builds history-threading alongside reuse (they must arrive together).
- **`resolve_allowed_documents(ctx)`** is the ONLY hook where V2 groups/grants permission logic slots in — MVP returns all org docs.
- **Chat (`app/services/chat/` + `app/models/chat.py`):** stateless was F40; F41 added persistence (migration 0009); F42 added the `message_traces` debug bundle (migration 0014) and split `services/chat.py` into `services/chat/repository.py` (Conversation/Message/MessageTrace repos) + `service.py` (pipeline logic) once it crossed the package-layout threshold — see "F42 Admin debug bundle" above for why this is the reference example for a repository/service-axis split (vs. `documents/`'s by-subdomain split or `ingestion/`'s by-pipeline-stage split).

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
| message_traces | 0014 | id, org_id, message_id (unique), hits jsonb, final_prompt, raw_output, created_at |

**Next migration: 0015.**

---

## Open questions / future decisions

- Reranker (4th seam) — add when real quality complaints arise in V2.
- F42 `message_traces` RESOLVED (this session) — built to architecture.md's locked schema exactly (hits/final_prompt/raw_output/created_at); no separate `latency_ms` column (that number is only ever logged via structlog's `chat.llm_call_succeeded`, never persisted — a future addition if trace-level latency reporting is ever needed).
- F52 SSE consumption RESOLVED: `fetch` + `ReadableStream.getReader()` + `TextDecoder`; buffer splits on `\n\n` to handle partial reads; `AbortController` in `useRef` for cleanup on unmount/re-submit. `EventSource` was NOT used (POST body required).
- V2 folder-permissions: per-folder role-based access (client stated as a real future need) — `folder_id` is already the stable FK anchor; no permission code exists yet.
- Orphan blob sweep — `ObjectStore.delete` not built (deliberately deferred, rides with the sweep feature).
