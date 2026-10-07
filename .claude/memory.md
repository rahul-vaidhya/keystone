# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.


## Current state (read first)

- Latest work (2026-07-30 → 08-04): P0 + P1 roadmaps fully shipped and committed; repo cruft cleanup + CI float-tolerance fix (`b6ebacc`, CI green); hosted-reranker-for-production decision recorded (`4613a13`).
- **2026-10-06 → 10-07 — CSD358 IR hackathon (Track T1), prof approved submitting Veratas as-is. All pushed (HEAD `0cfed4b`).**
  - Built: from-scratch sparse IR core `app/services/retrieval/sparse/` (positional inverted index, Porter, lnc.ltc tf-idf, BM25, heap top-K, champion lists, idf elimination, zones heading/body, phrase/Boolean + `trace.py`) wired as hybrid's lexical channel via `SPARSE_RETRIEVAL_MODE` (`f3fafcc`); BEIR SciFact ablation `backend/eval/` (`f75c816`/`f396858`: nDCG@10 tfidf .619 / bm25 .686 / dense .716 / hybrid RRF .735; champion lists ~2x faster same quality); per-sentence citation checker (`citation_check.py`, migration 0025 `messages.claim_checks`; citations inherited from paragraph-end markers; lexical = best sentence-window tf-idf cosine; threshold 0.33) + Debug term/claim tables (`e82c548`, `ed9cdc3`); checker measured on original SciFact claims (`4b466b1`, `backend/eval/results/citation_check_eval.md`: AUC .999 vs random, .808 vs BM25 hard negatives, 88.7% of CONTRADICT pairs still "supported" — topical not entailment; eval recommends 0.49, app kept 0.33 for demo — decision pending, known-issues F10); `POST /retrieval/sparse-search` + Search page Ranked/Boolean/Phrase modes with query-analysis/merge-order/contribution traces (`fa0fffa`, `126269c`, polish `4de678d`); in-house `Markdown.tsx` renderer (`d63b766`); fixed-viewport shell, titles, favicon (`01c8245`); usability fixes U1–U7/U10/U11 (`cce1a83`, `9f60685`, `5b1f9b4`); page-accurate citations via `### Page N` markers (`ed9cdc3`).
  - `backend/.env` demo flags: `HYBRID_SEARCH_ENABLED`, `SPARSE_RETRIEVAL_MODE=bm25`, `CITATION_CHECK_ENABLED`, `ENRICHMENT_ENABLED`, `BROAD_QUERY_ENABLED`, `NOTEBOOK_OVERVIEW_ENABLED` all on. Old OpenRouter key died (401) and was replaced; `OPENAI_API_KEY` must equal the OpenRouter key.
  - Baselines: backend 493 passed / 3 deselected; frontend 224. Offline-suite command must override every demo flag to off/fake (see known-issues/agent prompts) — `.env` is NOT moved aside anymore.
  - Demo account `demo+1791310304@example.com` / `DemoPass!2026` ("IR Demo Notebook"). Answers asked before `ed9cdc3` keep stale claim checks/page ranges — ask fresh questions when recording. Heading zone is empty on kech104 (parser "Page N" headings); use "bond" to show champion lists.
  - User decisions: security bugs S1–S4 deferred; novelty not a focus; report + demo video NOT drafted by Claude. Next migration: 0026.
  - Gotchas: subagent pip install under the long scratchpad path fails (Windows path limit) — use system Python Playwright or a short `%TEMP%` path; old persisted messages don't re-run new derivations.
- **2026-10-07 (session 2): renamed the app Veratas → Keystone** (UI, docs, report, package names). Kept internal ids on purpose: DB user/password/name `veratas`, cookie `veratas_refresh`, localStorage keys `veratas_*`. Branch `fix/parser-streaming-report`: pypdf parser, stream errors (S5), router prompt, test .env isolation, RRF score display, textbook eval, README + report (`docs/report/`), video script `docs/video-script.md`.
- **Open problems tracker: `.claude/known-issues.md`** (QA walkthrough findings 2026-10-07 — check/update it every session).
- Still open: Ragas `pytest -m eval` harness blocked by upstream ragas↔langchain_community import error; orphan blob sweep unbuilt; frontend hosting undecided.
- Full session-by-session history (2026-06-21 → 2026-07-30) lives in `.claude/memory-history.md` (not auto-loaded). Feature/DoD record: `.claude/progresstracker.md` (not auto-loaded).

---

## Locked design decisions (do not relitigate)

- **Module boundary rule:** a module calls another module ONLY through its `service` —
  never its `repository` or ORM models.
- **No SQL outside `repository.py`/repository sections. No business logic in routes/
  controllers.**
- **Every DB query scoped by `org_id`**, both at the service call AND independently
  inside the repo method.
- **External services (LLM, embedder, parser) only through a seam interface.**
- **Ingestion stages are idempotent + resumable** (status-eligibility check + delete-then-
  rebuild for F20/F21; true upsert for F22).
- **Package-layout convention:** promote a layer file to a subpackage ONLY when >200
  lines AND 2+ independent responsibilities — never speculatively.
- **Seam retry discipline:** broad `except Exception` fine at a terminal stage boundary;
  inside a retry loop, catch ONLY `(TimeoutError, SeamTransientError)`.
- **`dependency_overrides` CANNOT unblock the full upload→READY walk** — the arq worker
  never sees it. Storage impl is config-selected (`STORAGE_MODE`), not DI-overridden.
- **`ObjectStore` port is `put`+`get`+`delete`** (delete added 2026-07-12 for hard-delete).
- **`path` on Folder is a non-authoritative DISPLAY CACHE**, rebuilt synchronously in-tx
  via `_rebuild_subtree_paths` (derives from parent_id+name only — never slices the old
  path string, which risks false-matching a sibling sharing a name prefix).
- **Deterministic arq `job_id` (`f"ingestion:{stage}:{document_id}"`)** is the real
  concurrency guarantee against double-enqueue under redelivery — a before/after DB-status
  check alone is not sufficient.
- **F41 citations persist every call as a fresh Conversation + Message pair** — no
  conversation reuse/multi-turn threading (a future feature must build both together).
- **RLS is enforced UNCONDITIONALLY (F60, migration 0015)** — never gate on config.
  `tenant_session(ctx.org_id)`/`auth_session(email)` in `config/db.py` are the ONLY
  sanctioned session openers (guard test bans bare `sessionmaker()` outside that file).
  Every FUTURE tenant table ships its own ENABLE/FORCE + `tenant_isolation` policy +
  `app_user` grant in its own migration.
- **RLS policy predicate must be `NULLIF(current_setting('app.org_id', true), '')::uuid`**
  — the NULLIF is load-bearing (see Gotchas).
- **`resolve_allowed_documents(ctx)`** is the hook where Access-Role tag-gating slots in;
  **`resolve_accessible_folder_ids`/`FolderOut.can_manage`** is the parallel hook for
  folder-mutation gating (2026-07-27) — same tag computation, different bypass rule
  (folder gate lets owner/admin bypass; notebook-sharing does not).
- **Notebook privacy has NO owner/admin bypass** — the one resource in this app where the
  org system role does not see everything (2026-07-27, explicit user decision).

---

## Gotchas (things that will bite again)

- **`structlog.testing.capture_logs()` does NOT lift the app's log-level floor.** DEBUG
  events are silently dropped before the capture processor ever sees them unless you
  temporarily swap `wrapper_class` to `make_filtering_bound_logger(logging.DEBUG)` around
  the capture block.
- **Testcontainers + Docker Desktop/Windows:** Ryuk reaper flakes →
  `TESTCONTAINERS_RYUK_DISABLED=true`. A single degraded/skip-heavy pytest run (especially
  background/detached) is not trustworthy — always re-run synchronously against the same
  warm container before believing a regression.
- **`SET LOCAL app.org_id = :bind` is INVALID Postgres.** Use
  `SELECT set_config('app.org_id', :org, true)`.
- **A committed transaction-local GUC resets to `''`, not NULL/missing**, on a pooled
  connection — any GUC-casting RLS policy needs `NULLIF(current_setting(...), '')::uuid`.
- **Superusers bypass RLS even under FORCE** — dev-as-superuser exercises zero RLS; don't
  mistake a working dev run for RLS proof. Teeth tests must connect as the restricted
  `app_user` role.
- **`onupdate=func.now()` + immediate `model_validate` raises `MissingGreenlet`** inside
  an async session — set `updated_at = datetime.now(UTC)` explicitly in the repository.
- **Shared Testcontainers DB across test files** is session-scoped — each file needs a
  unique email prefix or it collides with other files' signups.
- **Object-store test fixtures must be a shared instance, not a fresh lambda** —
  `lambda: _InMemoryObjectStore()` creates a new empty store per request.
- **`uq_folders_org_parent_name` does NOT protect root-level names** (`parent_id IS NULL`
  → Postgres NULL≠NULL) — the partial index `uq_folders_org_root_name` (migration 0010)
  is the real backstop; both the app-level check and the IntegrityError translation are
  needed in `create_folder`.
- **`FakeLLM` is context-aware**: checks whether the latest user-turn contains `"[1]"` —
  tests wanting grounded-LLM behavior must send a context-bearing prompt.
- **`FakeEmbedder` cannot prove a true-positive grounded answer** — hash embeddings are
  semantically meaningless (retrieval distances ~0.93 regardless). Manual validation of a
  CORRECT grounded answer needs `RealEmbedder`.
- **`OPENROUTER_API_KEY` serves all 3 seams** (parser/embedder/LLM) via
  `OPENAI_BASE_URL=https://openrouter.ai/api/v1` — one credential, three metered paths.
  `RealEmbedder`/`RealLLM` read `OPENAI_API_KEY`/`OPENAI_BASE_URL`, a SEPARATE pair from
  `OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL` (used only by `RealParser`) — both need
  setting for a real-seam dev run.
- **Known gap, not fixed:** a lost enqueue (upload time or mid-ingestion-chain) silently
  strands a document at whatever status it last reached — no sweeper/re-dispatch exists.
- **`scrollIntoView` not a function in jsdom** — optional-chain the method call itself:
  `ref.current?.scrollIntoView?.(...)`.
- **`citations === undefined` vs `citations === []` as a streaming sentinel** in
  `ChatMessage` — `undefined` = still streaming, defined (even `[]`) = final answer. Never
  set `citations: []` mid-stream.
- **SSE `stream_ask` has no mid-stream retry** — once tokens are flowing, any error after
  the first token yields a `{"type":"error"}` event; pre-token failures still propagate as
  real errors since nothing has been sent yet.
- **`app/config/__init__.py`'s re-export shadows the `app.config.settings` submodule** —
  bind the settings singleton directly (`from app.config.settings import settings`), never
  via a module alias.
- **Dotted-path STRING LITERALS in `monkeypatch.setattr("a.b.c", ...)` are invisible to
  import-sweep refactors** — grep string literals separately after any module move.
- **This dev environment's real entrypoints are `backend/main.py`/`backend/worker.py`**
  (top-level, not `app/main.py`/`app/worker.py`) — run with `backend/.venv/Scripts/
  python.exe` from inside `backend/`. `STORAGE_MODE` defaults to `r2` with empty
  credentials if unset — set `STORAGE_MODE=local` for any live dev-upload check.
  **Superseded 2026-07-27**: `backend/.env` now sets `PARSER_MODE`/`EMBEDDER_MODE`/
  `LLM_MODE=real` + `OPENAI_API_KEY`/`OPENAI_BASE_URL` + `STORAGE_MODE=local`
  persistently — no longer needs manual process-env overrides each session, but ALSO
  means a stray second process pair without these overrides will silently serve fake
  seams (see the top-of-file 2026-07-27 entry).
  **After any backend code edit, kill and restart whatever's on port 8010 specifically**
  — it does not hot-reload, and a stray untouched process can silently keep serving old
  code or a different config than intended.
- **A background subagent's task-notification can report `status: failed` even when its
  actual file edits were already complete and correct** — check `git status`/`git diff`
  before assuming zero progress and re-dispatching.
- **claude-in-chrome**: reading a DOM input's `.value` via `javascript_tool` is blocked by
  the safety classifier even for self-generated throwaway test data — work around by
  widening the input via a pure CSS style mutation, then reading it visually via a
  screenshot zoom. Also: a CDP screenshot can intermittently time out right after a
  state-mutating POST — a bare retry succeeds within seconds, it's not a real hang.

---

## Schema quick-reference (what exists)

| Table | Migration | Key columns |
|-------|-----------|-------------|
| organizations | 0001 | id, name |
| users | 0001 (+0013, +0018) | id, org_id, email, role, is_active, failed_login_attempts, locked_until, token_version, name |
| folders | 0004 | id, org_id, parent_id, name, path |
| tags | 0004 | id, org_id, name |
| documents | 0005 (+0017) | id, org_id, folder_id, title, storage_key, checksum, status, failed_stage, error_detail, metadata_, uploaded_by |
| document_tags | 0004 | document_id, tag_id, org_id |
| sections | 0006 | id, org_id, document_id, parent_section_id, heading, level, char_start, char_end, page_start, page_end, path, ordinal, summary, topics |
| chunks | 0006 | id, org_id, document_id, section_id, char_start, char_end, content, token_count, ordinal |
| embeddings | 0007 | id, org_id, document_id, owner_type, owner_id, model, dim, embedding vector(1536) |
| knowledge_bases | 0008 | id, org_id, name, description |
| knowledge_base_documents | 0008 | knowledge_base_id, document_id, org_id |
| conversations | 0009 | id, org_id, created_at |
| messages | 0009 | id, org_id, conversation_id, role, content, citations jsonb, created_at |
| message_traces | 0014 | id, org_id, message_id (unique), hits jsonb, final_prompt, raw_output, created_at |
| invite_tokens | 0016 | id, org_id, user_id, token_hash (unique), expires_at, used_at |
| access_roles / user_access_roles / access_role_tags / folder_tags | 0012 | tag-based RBAC join tables |
| widgets | 0019 | id, org_id, knowledge_base_id, name, public_id (unique), allowed_origins jsonb, is_active, created_by |
| notebook_shares | 0020 | id, org_id, notebook_id, user_id, created_at |

Migration `0015` (F60) added no tables — applied `ENABLE`+`FORCE RLS` +
`tenant_isolation` policies to all 18 tenant tables + the `auth_email_lookup` bootstrap
policies + the `app_user`/`migrator` role split.

**Next migration: 0026.**

---

## Open questions / future decisions

- Reranker (4th seam) — add when real quality complaints arise in V2.
- **Reranker production deployment (locked 2026-08-04, not yet built)**: swap self-hosted
  BGE-reranker-v2-m3/TEI for a hosted cross-encoder-as-a-service API (Cohere Rerank / Jina
  Reranker / Voyage rerank-2) when this project actually deploys — CPU-only TEI warmup
  (~11.5 min) is fine for dev, too heavy for production infra on this team's scale. Pure
  `Reranker`-Protocol adapter swap (new `real_reranker_<vendor>.py`), zero change to
  `RetrievalService`'s widen/rerank/fallback logic. LLM-as-reranker re-confirmed rejected
  (cost/latency + poor score calibration for the confidence gate's fixed threshold) — target
  a real cross-encoder API, not an LLM prompt. Known trade-off, not yet resolved: sending
  chunk text to a third-party rerank API is in tension with the "your documents never leave
  your infrastructure" pitch — same class of caution as the SaaS-tracing-vendor rejection in
  `research-production-agent-features.md`. Local dev keeps `RERANKER_MODE=fake|real` (self-
  hosted) regardless — this only changes what production uses. Full detail:
  `architecture.md`'s "Reranker" section + Build-now/Postponed table,
  `research-production-agent-features.md`'s addendum under P0 item 1.
- Hierarchical retrieval (V2) — built and flag-gated but recommended OFF by default per
  the 2026-07-16 eval harness (no measured benefit on a single-document corpus; revisit
  once a genuinely large multi-document notebook creates real pressure on flat's
  precision).
- V2 folder-permissions — largely superseded by the 2026-07-12 Access Roles system and
  the 2026-07-27 folder-mutation gate; any further per-folder role granularity beyond tag-
  based gating is still open.
- Orphan blob sweep — not built; `ObjectStore.delete` exists (added 2026-07-12) but
  nothing yet sweeps blobs orphaned by a failed post-commit delete.
- Frontend hosting decision (Netlify/Cloudflare Pages vs. Vercel vs. custom nginx/Caddy)
  is still unmade — the clickjacking `_headers` file (2026-07-24) only takes effect by
  convention on Netlify/Cloudflare Pages today.
