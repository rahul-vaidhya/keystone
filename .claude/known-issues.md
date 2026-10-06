# known-issues.md — Open problems tracker

> Source: two Playwright user-walkthrough QA passes on 2026-10-07 (core single-user journey +
> team/admin/sharing flows). Update the Status column when something is fixed (add commit ref).
> Status: OPEN / IN PROGRESS / FIXED (`<commit>`) / WONTFIX (reason).

## Demo-critical (T1 hackathon demo)

| ID | Problem | Status |
|----|---------|--------|
| D1 | Claim checker noisy: LLM puts one `[n]` at the end of a paragraph/list item, so earlier sentences are flagged "uncited" although the cited chunk supports them; "weak" never appeared in 8 answers; scores not shown anywhere (no tooltip on supported, nothing in Debug panel) | FIXED (`ed9cdc3`) |
| D2 | Broad questions ("Summarize this chapter") refuse — `BROAD_QUERY_ENABLED` off; starter chip "Summarize the key points" likely refuses on large notebooks | FIXED (`ed9cdc3`; flags appended to backend/.env + enrich-backfill run) |
| D3 | Citation panel shows whole-doc "Pages 1–36" instead of the cited page; shows dev details (`chars 7053–8053`, raw `### Page 3` markdown) | FIXED (`ed9cdc3`) |
| D4 | Overview tab visible but `NOTEBOOK_OVERVIEW_ENABLED` off → Generate returns 409 | FIXED (`ed9cdc3`; NOTEBOOK_OVERVIEW_ENABLED=true in backend/.env, verified live) |
| D5 | Markdown not rendered in chat answers / Overview: LLM bold `**Heading**` shows raw asterisks in numbered lists (seen live 2026-10-07) | OPEN |
| D6 | Answers asked BEFORE `ed9cdc3` keep their old persisted claim checks (noisier "uncited" counts) — use fresh questions in the demo, or clear the demo notebook history | OPEN (note) |
| D7 | Broad-query answers take ~50s; broad answers cite parser page-label sections ("Page 1", "Contents") because `SEMANTIC_OUTLINE_ENABLED` is off | OPEN |

## Security (deferred by user decision 2026-10-07 — not fixing now)

| ID | Problem | Status |
|----|---------|--------|
| S1 | **Critical.** Notebook chat history (`GET /chat/notebooks/{id}/messages`) returns ALL users' messages for the notebook → members without an Access Role read restricted answers + cited chunk text; questions not private; anonymous embed-widget chats mixed in unlabeled | OPEN |
| S2 | **Critical.** Any member can `DELETE /documents/{id}` for a document inside an Access-Role-restricted folder (rename/move correctly 403). Also UI shows delete/move controls on that row | OPEN |
| S3 | **Critical.** Admins bypass notebook privacy via `GET /chat/messages/{id}/trace` and `POST /evals/golden-questions` (no notebook-access check) | OPEN |
| S4 | High. Members can `POST /documents/upload` with `folder_id` of a restricted folder (moving a doc in is correctly 403) | OPEN |
| S5 | Low. `/chat/stream` on a no-access notebook returns 200 + SSE "Stream failed" instead of 403 | OPEN |
| S6 | Low. Any member can list all org users (`/auth/users`) — possibly by design (share dialog) | OPEN |

## Usability

| ID | Problem | Status |
|----|---------|--------|
| U1 | High. Repository table clipped (`overflow-hidden`, table wider than container): delete × and move select unreachable at 1440/1920px; on mobile only Title+Uploaded visible | FIXED (cce1a83) |
| U2 | High. Folder with documents but no subfolders can't be deleted: `FolderTree.handleDelete` only opens cascade/reflow dialog when `folder.children.length > 0`; shows raw "pass mode=cascade…" text | FIXED (cce1a83) |
| U3 | Medium. Notebooks can't be renamed (no UI) | FIXED (9f60685) |
| U4 | Medium. Invalidated session (deactivation / password changed elsewhere) hangs on "Loading…" / "Failed to load documents." instead of redirecting to login | FIXED (9f60685) |
| U5 | Medium. Private/nonexistent notebook URL → endless "Loading…" + misleading "no documents yet" instead of access-denied / not-found | FIXED (9f60685) |
| U6 | Medium. Failed uploads show internal dev text ("RealParser supports PDF only… not built in F23", "Stream has ended unexpectedly"); file input accepts any type | FIXED (cce1a83) |
| U7 | Medium. Cascade-delete dialog says it deletes everything inside, but documents survive (moved to root) | FIXED (cce1a83) |
| U8 | Medium. Invite links unrecoverable (no resend/pending state; next invite replaces link on screen) | OPEN |
| U9 | Medium. Plain members have no UI to change password (form only on admin Users page) | OPEN |
| U10 | Low. Chat history API: user/assistant pair share `created_at` → order nondeterministic (needs tiebreaker) | FIXED (5b1f9b4) |
| U11 | Low. Duplicate upload gives no feedback | FIXED (cce1a83) |
| U12 | Low. No UI to tag documents or filter by tag (tags only creatable on Access Roles page, attachable to folders) | OPEN |
| U13 | Low. Every page title is "Veratas"; no favicon (console 404) | OPEN |
| U14 | Low. Accept-invite has no name field (greeting "Qa Membera+"); sidebar shows email not name | OPEN |
| U15 | Low. Server-side validation errors are raw Pydantic text; no confirm-password fields; "incorrect password" shown twice | OPEN |
| U16 | Low. Login is per-tab (sessionStorage) — new tab needs re-login | OPEN (by design?) |
| U17 | Low. Search returns 8 random chunks for gibberish (no relevance cutoff, no scores); search source panel lacks page info | OPEN |
| U18 | Low. Share dialog lists the owner as a share target; folder-move select is a flat list | OPEN |

## Test accounts left in dev DB (2026-10-07)
- Demo: `demo+1791310304@example.com` / `DemoPass!2026` (notebook a21dc7f1-…)
- QA core: `qa-core+1791310996@example.com` / `QaNewPass!2026y`
- QA team: owner `qa-owner+1791311134@example.com` / `QaPass!2345`; admin `qa-membera+1791311134@example.com` / `QaNewPass!6789`; member `qa-memberb+1791311134@example.com` / `QaPass!2345`
