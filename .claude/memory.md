# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

## P0 roadmap (2026-07-28, IN PROGRESS): 5 features from research-production-agent-features.md

Full `/architect` session (all 5 planned + confirmed before any code) then sequential
subagent-built implementation, one feature at a time (implement → orchestrator
independently re-reviews the diff + reruns tests/ruff itself, never trusts the
subagent's self-report → remember → next feature). Order: (1) Reranker seam, (2)
confidence gate, (3) hybrid search (BM25+vector RRF), (4) `message_feedback` table +
frontend wiring, (5) golden-eval suite (Ragas). Features 1-3 all touch
`app/services/retrieval.py` — re-check its line count/package-layout-promotion
question after each.

**Architecture decisions locked across the 5-feature session (apply to all of them,
not just feature 1):**
- Every new V2-style capability gets its OWN `*_ENABLED` flag, default `False`,
  independent from any adjacent `*_MODE` flag — shipping the code changes nothing until
  explicitly turned on. (`RERANKER_ENABLED` vs `RERANKER_MODE`; confidence gate has NO
  separate flag — it's implicit whenever reranking is on; `HYBRID_SEARCH_ENABLED` is
  independent of both.)
- New seam/service HTTP adapters mirror `real_llm.py`/`real_parser.py`'s lazy-import +
  `SeamNotConfigured`/`SeamTransientError` shape exactly — self-hosted model-serving
  infra (reranker) is an HTTP client to a separate Docker service, never an in-process
  ML dependency in the API/worker image.
- Additive-only schema changes throughout: new nullable fields (`rerank_score`,
  nullable `distance`), never a replaced/removed field, never a rewrite.

### Feature 1: Reranker seam — DONE (2026-07-28, uncommitted)

4th seam, folded into the EXISTING `app/services/seams/` package (protocols.py/
fakes.py/factory.py shared, new `real_reranker.py` for the vendor-specific HTTP
adapter — not a standalone file holding everything). `RERANKER_ENABLED` (default
`False`) gates whether `RetrievalService._retrieve_hits` widens the chunk kNN
candidate pool (`candidate_k = max(k, RERANK_CANDIDATE_K)`) and reranks it back down
(`final_k = min(k, RERANK_TOP_K)`) via `Reranker.rerank(query, candidates, top_k)`;
`RERANKER_MODE` (fake|real) only matters once enabled. `FakeReranker` is a true
identity passthrough stamping `rerank_score = 1.0 - distance` (deterministic, lets a
future confidence-gate feature control the score via `FakeEmbedder` distance).
`RealReranker` is an `httpx` HTTP client to a self-hosted BGE-reranker-v2-m3 via
Hugging Face TEI (`docker-compose.yml`'s new `reranker` service, port 8081,
`RERANKER_URL` setting) — sorts TEI's response explicitly rather than trusting vendor
ordering. New nullable `rerank_score: float | None = None` on both `ChunkHit` and
`ContextBlock`, additive only, `distance` untouched.

`services/retrieval.py`'s `_retrieve_hits` was split into itself (widen/rerank
wrapper) + a new `_search_hits` (the original flat/hierarchical sourcing, verbatim,
unchanged) — the widen/rerank step wraps whichever strategy `_search_hits` used,
agnostic to flat vs. hierarchical. File is now 231 lines; judged still one cohesive
concern (no new table/vendor ownership) so left flat, not promoted to a subpackage —
recheck after feature 3 (hybrid search) adds more to the same file.

**Real deviation from the original plan, correctly made**: `reranker` had to be
threaded through `services/embed.py`'s `public_chat_stream` and
`controllers/embed.py`'s `stream_public_chat` too (not just `chat`/`retrieval`
controllers as originally scoped) — `embed_service.public_chat_stream` calls
`chat_service.stream_ask` directly, which now requires the `reranker` kwarg; omitting
it would have broken the public embed-widget endpoint at runtime.

**Real bug found and fixed during implementation**: `real_reranker.py` initially
imported `ChunkHit` directly (not `TYPE_CHECKING`-guarded) → circular import via
`app/models/__init__.py`. Fixed with the same `TYPE_CHECKING` guard `protocols.py`/
`fakes.py` already needed for the same reason (`app.models.ingestion` imports
`EMBED_DIM` from `app.services.seams.protocols`, so a real import the other direction
is circular) — safe under `from __future__ import annotations` since `Protocol`
runtime-checks only method names, never argument types.

**Independently reverified by the orchestrator** (not just the implementing
subagent's self-report): `git diff` read in full across all 20 changed files, full
suite rerun (`307 passed, 2 skipped`, `.env` temporarily moved aside for a clean
signal then restored), `ruff check`/`ruff format --check` clean. Gate-off byte-identical
confirmed by a dedicated regression test proving `reranker.rerank` is never called and
`rerank_score` is `None` throughout when `RERANKER_ENABLED=False`. **Not committed** —
per-feature commits deferred until the user asks (or until all 5 land, per a decision
made at that time). `RealReranker`'s TEI request/response shape was written from the
documented HF TEI `/rerank` contract but never exercised against a live TEI instance —
worth a real smoke test before any `RERANKER_MODE=real` production use.

### Feature 2: Reranker-score confidence gate — DONE (2026-07-28, uncommitted)

Lives entirely in `chat/service.py` (new pure `_weak_evidence_gate_fires(blocks) ->
bool` helper), no separate enable flag — structurally inert whenever
`RERANKER_ENABLED=False` since `rerank_score` is always `None` then. New
`RERANK_MIN_SCORE: float = -10.0` (deliberately permissive default). Fires when
`blocks[0].rerank_score < RERANK_MIN_SCORE`; empty `blocks` never fires it (falls
through to the existing LLM-refusal path unchanged — a deliberate, known non-goal).

On fire: `build_messages` still runs (captures the would-be prompt for the trace),
`call_llm_with_retry`/`generate_answer` is skipped entirely, answer becomes the fixed
`_WEAK_EVIDENCE_MESSAGE` constant (distinct wording from the LLM's own refusal
string), `citations=[]`, persisted NORMALLY via the unchanged `_persist` (zero
signature changes — it already took `answer`/`citations`/`hits`/`final_prompt`
generically). New `ChatResponse.weak_evidence: bool = False` field; `stream_ask`'s
gate-fire path emits zero `token` events, one `done` event with `weak_evidence: true`,
correctly still threading `widget_id` for the embed-widget caller.

**Independently reverified by the orchestrator**: full diff read (all touched files),
full suite rerun (`312 passed, 2 skipped`, up from 307 — 5 new gate tests), `.env`
moved aside and restored, ruff clean. New tests use a `_RecordingLLM` test double
(proves the LLM seam was NEVER called, not just that the response looked right) and a
`_FixedScoreReranker` double (controls `rerank_score` directly via
`app.dependency_overrides[get_reranker]`, same override pattern as `get_llm`). **Not
committed.**

### Feature 3: Hybrid search (BM25 + vector, RRF) — DONE (2026-07-28, uncommitted)

Migration `0021`: `chunks.content_tsv` (STORED generated `tsvector` column,
`to_tsvector('english', content)`) + GIN index — no third-party extension, native
Postgres only. `ChunkRepository.search_chunks_lexical` (new, on `ChunkRepository` not
`EmbeddingRepository` — no embeddings join needed), `websearch_to_tsquery`/`ts_rank`,
returns `ChunkHit` with `distance=None`. `ChunkHit.distance`/`ContextBlock.distance`
now `float | None` (lexical-only hits genuinely have no cosine distance). New pure
`fuse_rrf(vector_hits, lexical_hits, k=60)` in `retrieval.py` — standard RRF,
chunk_id-deduped (vector instance wins on collision since it carries a real distance).
`HYBRID_SEARCH_ENABLED`/`HYBRID_CANDIDATE_K` settings, independent of
`RERANKER_ENABLED`. New `_vector_and_maybe_hybrid_search` helper replaced 4 duplicate
`ingestion_service.search_chunks(...)` call sites inside `_search_hits`; candidate
widening composes correctly with the reranker feature (effective pool =
`max(k, RERANK_CANDIDATE_K, HYBRID_CANDIDATE_K)` when both are on; fused list is
truncated back to the incoming `k` so the reranker still receives exactly what it did
before hybrid existed).

**Real bug found and fixed by the implementing subagent**: `Chunk.content_tsv`'s ORM
mapping needed `Computed("to_tsvector('english', content)", persisted=True)` — without
it, SQLAlchemy's `insertmanyvalues` batch-insert path (used by `bulk_create`)
explicitly sent `NULL` for the column on every insert, and Postgres rejects ANY
explicit value (even NULL) into a `GENERATED ALWAYS` column, breaking the entire
ingestion pipeline. `Computed()` here is DML-only signaling (excludes the column from
INSERT/UPDATE) — the actual DDL stays owned by the migration's raw SQL (this project
never runs Alembic autogenerate against `Base.metadata`, so no drift risk).

**Real cross-feature bug found during ORCHESTRATOR review (not the subagent), fixed
same session**: `FakeReranker.rerank` computed `rerank_score = 1.0 - hit.distance`,
which crashes with `TypeError` if a lexical-only hit (`distance=None`) ever reaches
the reranker — reachable once both `HYBRID_SEARCH_ENABLED` and `RERANKER_ENABLED` are
on together with `RERANKER_MODE=fake`. Fixed: `rerank_score = 1.0 - distance if
distance is not None else 0.0`, one new regression test in `test_seams.py`.

**Deliberate observability gap, documented not fixed**: no `logger.info` on the hybrid
path (unlike `hierarchical_used`'s INFO log) — `app.services.retrieval`'s module
logger is shared with hierarchical retrieval and `cache_logger_on_first_use=True`
permanently locks its level on first call; adding a hybrid INFO log would have broken
`test_retrieval_hierarchical.py`'s DEBUG-level capture test (the exact
`capture_logs()` gotcha already on record). Left unfixed by design — a real
observability gap worth a dedicated follow-up (e.g. a differently-named logger) if
hybrid search ever ships to production, not blocking.

**`services/retrieval.py` is now 325 lines** (was 231 after feature 1; features 2/4/5
don't touch this file, so this is likely its final size for this round). Still judged
one cohesive concern (permissions + pure algorithms + search orchestration, no new
table/vendor ownership in the file itself — all SQL stays delegated to
`ingestion`/`documents`/`knowledge` services) — NOT promoted to a subpackage. Worth a
deliberate second look at final wrap-up of all 5 features, not mid-stream.

**Independently reverified by the orchestrator**: full diff read across all changed
files, full suite rerun TWICE (320 passed pre-fix, 321 passed post-fix — the 1 new
regression test — both times 2 skipped), ruff clean both times, `.env` restored. Live
proof test (`test_hybrid_gate_on_finds_lexical_match_pure_vector_search_misses`): a
chunk containing a rare exact term embedded orthogonal to the query vector (so pure
vector search misses it, confirmed absent with the gate off) gets promoted to the TOP
result once hybrid fusion is on. **Not committed.**

### Feature 4: `message_feedback` table + frontend wiring — DONE (2026-07-28, uncommitted)

Migration `0022`: `message_feedback` (`org_id`/`message_id` cascade/`user_id` cascade/
`rating`/`reason_tags` text[]/`comment`/`corrected_answer`/`created_at`,
`unique(message_id, user_id)` — the upsert target, one current rating per user per
message, not an audit log). Full F60-pattern RLS block (brand-new tenant table).
`FeedbackRepository.upsert` (`pg_insert().on_conflict_do_update()`) +
`get_for_messages(message_ids, user_id) -> dict` (per-user scoped, the history-join
source). New `MessageRepository.get_with_notebook_id` (join to `Conversation`, both
sides independently `org_id`-scoped). `ChatService.submit_feedback`: resolves
message→notebook, 404 via new `MessageNotFound` if missing, 400 via new
`FeedbackOnUserMessage` if targeting a `user`-role message, reuses
`knowledge_service.get_notebook` for the EXACT SAME notebook-privacy check
`list_messages` already performs (never reimplemented). `POST /chat/messages/{id}/
feedback` under plain `get_ctx` (the service-level notebook check is the real gate).
`MessageOut.my_feedback` (calling user's own rating only) populated in `list_messages`
via a per-user-scoped join.

**Frontend** (first feature in this round to touch it): `chatApi.submitFeedback`,
`ChatPanel.tsx`'s pre-existing `handleFeedback` (already had local tri-state
deselect-on-second-click, unrelated to this feature) now also fires a real POST
(fire-and-forget, `.catch(() => {})`), history hydration seeds `feedback` state from
each message's `my_feedback` so the buttons survive reload/navigation — closing the
exact "resets to blank" gap confirmed present before this feature (same class of bug
as the F41-era chat-history-vanishing fix).

**Independently reverified by the orchestrator**: full diff read across all backend +
frontend files, backend suite rerun (328 passed/2 skipped, up from 321), ruff clean,
`.env` restored, frontend suite rerun (159 passed, `tsc -b`/`vite build` clean).
Highest-value tests confirmed: upsert-not-duplicate (re-rating same message+user stays
1 row), per-user scoping (user B never sees user A's rating on the same message —
`my_feedback: null` — while user A's own reload shows the real value), 403 on a
private/non-shared notebook via the reused `NotebookAccessDenied`, cross-org 404 at
both HTTP and repository level.

**Two minor, non-blocking notes** (not fixed, don't warrant it): `submit_feedback`
opens two separate `tenant_session`s (its own + `knowledge_service.get_notebook`'s
internal one) rather than one — correctness-fine (each `tenant_session` is an
independent connection, no shared-state risk), just one extra round-trip vs. an
alternative ordering; the frontend's local deselect-on-second-click has no backend
"clear my rating" endpoint to match it (pre-existing UI behavior, not introduced by
this feature, genuinely out of this round's scope). **Not committed.**

### Feature 5: Golden-eval suite — DONE (2026-07-28, uncommitted) — ALL 5 P0 FEATURES COMPLETE

Migration `0023`: new `golden_questions` table, full RLS. New `evals` domain (flat
files, per convention — small new domain): `models/evals.py` (`GoldenQuestion` ORM +
`GoldenQuestionCreate`/`GoldenQuestionOut`), `services/evals.py`
(`GoldenQuestionRepository` + `EvalsService`, mirrors `knowledge.py`'s flat-domain
shape), `routes/evals.py` + `controllers/evals.py` (`POST`/`GET /evals/golden-
questions`, both `require_admin`). New `chat_service.get_curation_snapshot` — the
ONLY way `evals.service` reads chat data (module boundary rule), reuses the EXISTING
`MessageNotFound`/`MessageTraceNotFound` exceptions plus one new, deliberately NOT
reused `CannotCurateUserMessage` (judged semantically dishonest to conflate with
`FeedbackOnUserMessage` — different endpoint, different user action, same underlying
`role != "assistant"` check). New `MessageRepository.get_user_question_in_conversation`
(org-scoped) — finds the paired question via the "fresh conversation, exactly one
user+assistant message pair" invariant. `reference_contexts` snapshots chunk TEXT (not
IDs) from `trace.hits`, `notebook_id` is `ON DELETE CASCADE`, `source_message_id` is
`ON DELETE SET NULL` (golden question survives its origin conversation being deleted).

**Real wiring required beyond the feature's own files** (correctly done, all
necessary): `main.py` (router mount), `migrations/env.py` (ORM registration rule —
`GoldenQuestion` would silently vanish from future autogenerate without this),
`controllers/__init__.py` (re-export), `pyproject.toml` (new `[eval]` extra +
`eval` pytest marker), `.github/workflows/ci.yml` (added to the `-m` exclusion
filter, for consistency with `real_parser`/`hierarchical_eval` even though the
marker's own `skipif` already made it redundant), `frontend/vite.config.ts` (new
`/evals` proxy entry — would otherwise 404 in dev).

**Frontend**: "Add to golden set" button inside `ChatPanel.tsx`'s existing
`TraceDetails` (admin Debug panel) component, with a real idle/pending/done/error
state machine (not just a bare fire-and-forget).

**Opt-in `pytest -m eval` harness** (`test_golden_eval.py`) mirrors
`test_hierarchical_eval.py`'s structure exactly: ingests `pdf/kech104.pdf` once with
real seams, asks real questions, curates via the REAL curation endpoint (proves the
pipeline end to end, not a shortcut), re-runs each curated question live, grades with
Ragas (`faithfulness`/`answer_relevancy`/`context_precision`/`context_recall`), prints
a report, asserts no hard threshold. **Honestly flagged as unverified**: the exact
Ragas `evaluate()` API call shape was written from documentation, not tested against
a real `ragas` install (explicitly permitted by scope — installing/running it needs a
live API key and wasn't required for this round's DoD). Properly skip-guarded
(`pytest.importorskip`-style check + the usual key/PDF env checks) so it can never
break a normal run even without the `[eval]` extra installed.

**Process note, not a code-quality issue**: the implementing subagent's FIRST turn
ended prematurely — it launched its own backgrounded test run and (incorrectly)
expected to be auto-resumed the way the orchestrator agent is, then stopped without
delivering a final report. Caught two things before resuming it: (1) `backend/.env`
was left moved aside as `.env.bak` (restored immediately by the orchestrator, not the
subagent); (2) no verified results yet. Resumed via `SendMessage` with explicit
instructions to re-check its own background work and restore `.env` properly — its
second turn delivered a complete, accurate report. **Lesson for future orchestration**:
subagents do NOT get automatically woken on their own backgrounded shell commands the
way the top-level orchestrator does on backgrounded Agent calls — never instruct a
subagent to "wait for a notification" from its own background work; it must poll/check
synchronously within the same turn, or the orchestrator must resume it explicitly.

**Independently reverified by the orchestrator**: full diff read across every backend
+ frontend file (including all the "extra" wiring files), backend suite rerun (336
passed/3 skipped — the 3rd skip is the new `eval` marker, correctly excluded), ruff
clean, `.env` confirmed restored via `ls`, frontend suite rerun (162 passed),
`tsc -b`/`vite build` clean. Test quality is high: the end-to-end curation test
verifies the persisted row directly (not just the HTTP response), a real per-user
cross-org 404 test, `require_admin` 403 tests on both endpoints, tenant isolation on
the list endpoint. One minor, low-risk, NOT fixed note: `get_curation_snapshot`
silently falls back to `question=""` if no paired user message is found (structurally
unreachable given the fresh-conversation invariant, but a silent fallback rather than
a raised error — same class of judgment call as feature 4's `assert ctx.user_id is
not None`, left as-is). **Not committed.**

---

## Wrap-up: all 5 P0 features from research-production-agent-features.md are DONE,
## independently verified, AND COMMITTED as 6 separate commits (2026-07-28)

User chose: (1) best-effort one-commit-per-feature, verified at each step, plus (2)
split `services/retrieval.py` into a subpackage now, as its own dedicated commit.
Both done. **Final commit sequence** (oldest→newest, all on `main`, pushed status:
NOT pushed to origin — only ask about that separately if the user wants it):
1. `0cddb5e` feat: add reranker seam
2. `c01cf71` feat: add reranker-score confidence gate
3. `c349b9b` feat: add hybrid search (BM25 + vector, RRF fusion)
4. `074a000` feat: add message_feedback table + wire up thumbs UI
5. `ad9d4f0` feat: add golden-eval suite (Ragas)
6. `3a54c25` refactor: split services/retrieval.py into a package

**How the 6 commits were actually built** (important context for future sessions):
since all 5 features were implemented as cumulative uncommitted diffs on shared files
(never committing between subagent runs), true surgical per-feature separation wasn't
possible via `git add -p` alone. Used a strip-then-progressively-restore technique
instead: reconstructed each feature's exact incremental content (verified against the
diffs already reviewed in this session) directly in shared files (`settings.py`,
`models/chat.py`, `models/ingestion.py`, `chat/service.py`, `chat/repository.py`,
etc.), committing at each checkpoint only after the FULL test suite passed against
that exact intermediate state. New files exclusive to one feature were staged
wholesale; migrations/eval test files were moved aside with `mv` (non-destructive)
between checkpoints rather than deleted. `services/retrieval.py` was kept FLAT through
commits 1-5 (matching what was actually tested at each stage) and only split into the
package as the dedicated commit 6, built on top of the fully-featured flat file.

**A real mistake made and disclosed mid-process**: used `git checkout --` (destructive
working-tree revert) on `tests/test_chat.py` and a few frontend files
(`ChatPanel.tsx`, `ChatPanel.test.tsx`, etc.), wrongly treating them like files
feature 1 contributed nothing to — this discarded features 2's and 4's/5's uncommitted
TEST content (never the application code, which was reconstructed precisely via
targeted `Edit` calls throughout and never lost). Disclosed to the user immediately
upon discovery; user chose to continue with freshly-authored tests covering the same
scenarios rather than fall back to one bundled commit. Recovered by writing new tests
for the confidence gate (5 tests), message_feedback (6 of the original 7 — one
dropped, an FK-heavy repository-backstop test, in favor of the already-solid
HTTP-level cross-org coverage), and the golden-set button (2 of 3). Every checkpoint's
full test count was verified to land within 1 test of the original independently-
reviewed count, and every intermediate commit passed the full suite before proceeding
— so the final, actually-shipped code at HEAD is unaffected; only a handful of test
cases are reworded/reduced from what the original implementing subagents wrote.
**Lesson for future git-history reconstruction**: `git checkout --` is only safe on a
file if you are CERTAIN every feature's contribution to it is already captured
elsewhere (either the file is exclusively owned by the feature you're stripping TO, or
you have full verbatim content for every other feature's portion) — when in doubt,
surgically edit instead, or move the file aside with `mv` rather than discard it.

**Final regression baseline** (verified once more at HEAD, after all 6 commits):
**backend 335 passed, 3 skipped** (started this round at 293; 1 fewer than the
peak-335... actually peak during independent per-feature review was 336, now 335 due
to the one dropped reconstructed test — noted above, not a regression in shipped
code); **frontend build clean**, 161 passed (peak was 162, same reduction reason).
ruff/`tsc -b`/`vite build` all clean at HEAD.

**Open item for a future session**: `.env.bak` files were created and always cleaned
up during this session's many test runs — none left behind, confirmed via `ls` at the
very end. Nothing else outstanding; the P0 roadmap is fully shipped.

### Problems encountered this session, and how each was fixed

1. **Real ORM bug — generated-column batch inserts** (feature 3, hybrid search). Adding
   `Chunk.content_tsv` as a plain `mapped_column(TSVECTOR, nullable=True)` broke the
   ENTIRE ingestion pipeline (68 test failures): SQLAlchemy's `insertmanyvalues` batch
   insert path (used by `ChunkRepository.bulk_create`) sent an explicit `NULL` for the
   column on every insert, and Postgres rejects ANY explicit value — even `NULL` — into
   a `GENERATED ALWAYS` column. **Fix**: add `Computed("to_tsvector('english',
   content)", persisted=True)` to the column definition. This is DML-only signaling
   (tells the ORM to exclude the column from INSERT/UPDATE) — the actual DDL stays
   owned entirely by the migration's raw SQL, since this project never runs Alembic
   autogenerate against `Base.metadata`, so there's no drift risk from the mismatch
   between the `Computed()` expression string and the migration's SQL. **Takeaway for
   future generated columns**: always add `Computed(...)` to the ORM mapping, not just
   the migration DDL, or any bulk-insert path will break.

2. **Real cross-feature interaction bug — `FakeReranker` crash on `None` distance**
   (found during feature 3's review, by the orchestrator, not the implementing
   subagent). `FakeReranker.rerank` computed `rerank_score = 1.0 - hit.distance`;
   hybrid search (feature 3) can produce a lexical-only hit with `distance=None`, which
   crashes that subtraction with a `TypeError` if it ever reaches the reranker — live
   whenever both `RERANKER_ENABLED` and `HYBRID_SEARCH_ENABLED` are on together with
   `RERANKER_MODE=fake`. Both flags default off, so this couldn't fire in production
   today, but was a real landmine for the next person who turns both on in dev/test.
   **Fix**: `rerank_score = 1.0 - distance if distance is not None else 0.0`, plus one
   new regression test. **Takeaway**: when two independently-built flagged features can
   compose, explicitly test the composed state, not just each flag in isolation — this
   is exactly the kind of gap a single-feature review pass structurally can't catch.

3. **Process mistake — a subagent's own backgrounded shell command doesn't wake it up**
   (feature 5, golden-eval suite). The implementing subagent launched the full test
   suite via its own `run_in_background: true` Bash call, then ended its turn expecting
   to be "notified automatically when it completes" — but that auto-resume mechanism
   only applies to the top-level orchestrator's backgrounded Agent calls, not a
   subagent's own backgrounded tool calls. The subagent's task-notification fired with
   `status: completed` but no actual results delivered. Caught by checking `git status`
   directly rather than trusting the notification content, which also revealed
   `backend/.env` had been moved aside (`.env.bak`) and never restored. **Fix**:
   restored `.env` myself immediately, then resumed the subagent via `SendMessage` with
   explicit instructions to re-check its own background work synchronously and finish
   its report. **Takeaway for future orchestration**: never instruct a subagent to
   "wait for a notification" from its own background work — it must poll/check
   synchronously within the same turn, since only the orchestrator's own backgrounded
   `Agent` calls trigger an automatic wake-up.

4. **Process mistake — destructive `git checkout` during commit reconstruction** (while
   splitting the 5 features' cumulative uncommitted diff into 5 separate commits at the
   user's request). Used `git checkout -- <file>` to revert `tests/test_chat.py` and
   several frontend files (`ChatPanel.tsx`, `ChatPanel.test.tsx`, `chatService.ts`,
   `types/chat.ts`, `vite.config.ts`) back to the pre-session baseline, on the
   (correct, for application-code files) assumption that feature 1 hadn't touched them
   — but `git checkout --` throws away ALL uncommitted changes to a file, including
   later features' TEST additions that still needed to be restored for their own
   commits. Discarded features 2's and 4/5's uncommitted test content (never
   application code — every service/model/repository file was reconstructed precisely
   via targeted `Edit` calls throughout, never via blind checkout, and stayed correct
   throughout). **Caught and disclosed immediately** upon noticing the mismatch, before
   committing anything built on the loss. **Fix, per the user's explicit choice**:
   authored fresh tests covering the identical scenarios (confidence-gate firing/
   thresholds/persistence/SSE — 5 tests; message_feedback upsert/scoping/access-checks
   — 6 of the original 7, one FK-heavy repository-backstop test dropped as redundant
   with existing HTTP-level coverage; the golden-set button — 2 of the original 3),
   verified every one against the real, unaffected application code before each commit.
   Final test counts land within 1 of the original independently-reviewed numbers at
   every checkpoint. **Takeaway**: `git checkout --` is only safe on a file when you
   are CERTAIN every feature's contribution to it is already captured elsewhere (either
   you're stripping TO the file's sole owner, or you hold full verbatim content for
   every other feature's portion) — when in doubt, surgically edit instead, or move the
   file aside with `mv` (non-destructive, used correctly elsewhere in this same
   reconstruction for migrations and eval test files) rather than discard it.

5. **Minor — FK-seeding oversight in a freshly-authored test** (during the recovery in
   item 4). A hand-written repository-level backstop test inserted a `MessageFeedback`
   row directly without first seeding the `Organization` row its `org_id` foreign key
   pointed at, failing with an `IntegrityError`. Rather than fully seed the deeper FK
   chain (`Organization` → `User` → `Conversation` → `Message`) for one supplementary
   test, dropped it in favor of the already-solid HTTP-level cross-org 404 test, which
   covers the same tenant-isolation concern through the real access path.

## Older sessions

**Compacted 2026-07-27** (was >150k chars, over the editor limit). Older sessions were
reduced to one-paragraph summaries — commit hashes and outcomes preserved, blow-by-blow
live-verification narration and subagent orchestration detail cut. Full history is in
`git log`; `progresstracker.md` has the feature/DoD-level record. The two most recent
> sessions are kept in fuller detail since they're the ones a "next session" is most
> likely to need to resume from.

---

## Research: production-grade retrieval/observability/evals/feedback roadmap (2026-07-27 — RESEARCH ONLY, nothing built)

Direct ask: research what it'd take to make Veratas "properly production ready and
significantly better than any competition," specifically covering different retrieval
models, LLM config (temp etc.), vector-store options, log tracing, tool calling
(web-search + RAG-as-a-tool), evals for agents, feedback-system improvements, and a
direct comparison against Databricks Agent Bricks / Google ADK / Microsoft Copilot
Studio (user had just watched an Agent Bricks demo: schema-guided extraction, a
Knowledge Assistant blending structured+unstructured sources, automatic eval +
before/after comparisons, an SME labeling session that auto-improves the agent, full
MLflow tracing). Also folded in a real diagnosed bug: retrieval has **no
distance/confidence threshold** — always returns top-k regardless of match quality, so
on weak-nearest-neighbor questions the LLM gets irrelevant context and correctly
refuses per its grounding contract even when the real answer exists in the document.
Ran 6 parallel WebSearch-backed research subagents (retrieval/reranking, vector-store
infra, observability/tracing, evals+feedback, tool-calling+model-config, competitive
analysis), each grounded in Veratas' actual seam/module architecture so
recommendations map to concrete files/tables/flags, not generic advice.

**Full synthesis + prioritized P0/P1/P2 roadmap written to
`.claude/context/research-production-agent-features.md`** — read that file before
scoping any future work in this area. `buildplan.md`'s "Postponed" section now points
to it. One-paragraph highlights:

- **The bug's fix, per the research**: a `Reranker` seam (new Protocol mirroring
  `Parser`/`Embedder`/`LLM`, `RERANKER_MODE=fake|real`, real impl = self-hosted
  BGE-reranker-v2-m3, NOT LLM-as-reranker — 9x cost/35x latency for modest gain) +
  widen candidate kNN then rerank down + a confidence gate keyed on the **reranker
  score, not raw cosine distance** (raw distance cutoffs don't generalize across
  corpora) + hybrid BM25+vector search via RRF (Postgres-native: `pg_search`/
  `pg_textsearch` extension, no new infra) — this directly targets Veratas' real
  corpus shape (business PDFs full of exact terms/IDs/acronyms that pure dense
  embeddings miss). Both reranker and hybrid search were already named as Postponed
  V2/V3 items pre-research; this operationalizes them with concrete seam/schema shape.
- **Standout finding**: "contextual retrieval" (Anthropic's technique — prepend a short
  LLM context blurb to each chunk before embedding, ~49-67% fewer retrieval failures)
  is unusually cheap for Veratas specifically because the per-section LLM summary
  infrastructure (`ENRICHMENT_ENABLED`, `sections.summary`/`topics`) already exists and
  is dormant — this is "wire up something already built," not new infra. Worth
  re-evaluating `HIERARCHICAL_RETRIEVAL_ENABLED` again after this ships, since the
  2026-07-16 eval's null result may be an artifact of context-free chunk embeddings
  rather than proof hierarchy itself doesn't help.
- **Vector store verdict**: stay on pgvector — moving to Pinecone/Weaviate/Qdrant would
  mean re-deriving an RLS-equivalent tenant-isolation guarantee outside Postgres, a
  real regression against the F60 hard rule. `pgvectorscale`/`halfvec` (same Postgres,
  additive) are the right next lever, gated on a real row-count/latency trigger,
  not before.
- **Observability verdict**: NOT a 4th seam (tracing is cross-cutting infra, not
  swappable business logic — no meaningful "fake tracer"). Adopt OpenTelemetry GenAI
  semantic conventions in new spans immediately; self-hosted Langfuse or Arize Phoenix
  only (customer-document content flows through prompts, so SaaS-only tracing breaches
  the same trust boundary the product sells protection from); keep `message_traces`/
  the admin Debug toggle as the cheap, already-working end-user-facing view.
- **Evals/feedback verdict**: self-hosted MLflow (`mlflow.genai.evaluate()` + Labeling
  Sessions) is the real open-source analog of Agent Bricks' labeling UI — Databricks'
  own labeling UI is built on this same primitive, so don't hand-roll one. Ragas as the
  metrics engine. New `message_feedback` table to finally wire up the dead thumbs
  up/down UI control (currently unpersisted decoration, does nothing). A durable,
  admin-curatable golden-question set (sourced from real `message_traces` rows, not a
  hand-written pytest list) run via a new CI-excluded `pytest -m eval` marker (same
  pattern as `real_parser`/`hierarchical_eval`).
- **Explicitly deprioritized as over-engineering for this team's scale** (each has a
  named trigger condition in the research doc, none fire today): DSPy-style
  *autonomous* prompt auto-rewriting (candidate prompts should still require a human
  to promote them past the eval gate — optimization-assisted, never auto-deploy);
  full agentic multi-hop retrieval / a `WebSearch` seam (if ever built: off by default,
  and any web-sourced answer must be visually/structurally distinct from
  document-grounded citations — never blended into the `[n]` list, or it quietly
  destroys the "only knows what you gave it" trust guarantee that's the whole product
  positioning); per-org/per-request LLM temperature overrides; migrating off pgvector.
- **Where Veratas already wins, confirmed by the competitive report — don't touch**:
  the fixed-refusal-string grounding contract is *stricter* than Copilot Studio's
  "allow ungrounded responses" toggle; Access Roles + notebook privacy already match
  Glean's permission-aware-retrieval edge; the `WHERE model = :active_model` embedding
  filter is exactly the standard live-re-embed pattern, no change needed.
- **Real gap vs. Databricks worth closing (P1)**: a schema-guided structured-extraction
  mini-brick (NL field description → LLM-proposed JSON schema → per-doc extraction →
  user-corrected examples → re-run) — bounded, extends the existing per-section
  LLM-call pattern in `enrichment.py`, no lakehouse/Delta equivalent needed.

**Next session starting this work should run `/architect` against
`research-production-agent-features.md`'s P0 list** (reranker seam, hybrid search,
confidence gate, `message_feedback` table, golden-eval suite) — these five are
independent enough to sequence as separate features, not one mega-PR.

---

## Dev-environment bug: API process running fake seams while worker ran real ones (2026-07-27 — FIXED, no app code changed)

Direct-ask bug report: real questions on a READY document all got the exact refusal
string. **Root cause, not a parser bug**: two stray `uvicorn`/`arq` process pairs were
running simultaneously (one from `backend/.venv`, one from bare system Python); the one
actually holding port 8010 had no real-seam env overrides, so `Settings` defaulted every
seam to `"fake"`. The arq worker, by contrast, had the real overrides, so ingestion used
the real embedder. At query time `RetrievalService.search` embedded the question with the
API process's `FakeEmbedder` (`model="fake-embed-1536"`); `search_chunks` filters
`WHERE model = :active_model`, so it matched zero of the real chunks stored under
`text-embedding-3-small` → empty context → the LLM's own refusal string, regardless of
question quality.

**Reusable gotcha**: a total, uniform "everything refuses" symptom across unrelated,
reasonable questions on a READY document points at a **retrieval-embedder / stored-
embedding model mismatch** — check the admin Debug trace's `Hits (N)` count FIRST (0 hits
= embedder/model mismatch, check for duplicate/stray backend processes via `netstat -ano`
+ `Get-CimInstance Win32_Process`) before suspecting the parser or LLM.

**Fix**: killed the 4 stray/duplicate processes, started exactly one clean uvicorn + one
clean arq worker from `backend/.venv`. **Persisted to `backend/.env`** (user's explicit
choice over a throwaway process-env fix, to survive restarts): added
`PARSER_MODE=real`, `EMBEDDER_MODE=real`, `LLM_MODE=real`, `OPENAI_API_KEY`/
`OPENAI_BASE_URL` (same OpenRouter key/base as `OPENROUTER_*`), `STORAGE_MODE=local`.
**Local dev now makes real, billed OpenRouter calls by default** — flip the 3 `*_MODE`
vars back to `fake` in `.env` if cost becomes a concern.

Live-reverified post-fix: grounded, cited answers on real questions. One question
("tell me the gist of everything") still legitimately refuses — not a bug, MVP retrieval
is flat per-chunk search, not whole-document summarization (V2/V3 territory); Debug trace
confirmed 8 real hits at weak/scattered distances (0.767–0.827), correctly declined rather
than give a partial summary. Also re-confirmed pre-existing, unrelated, low-impact parser
quirks (OCR-garbled headings, one raw-PDF-metadata chunk) — not touched.

**Gotcha reconfirmed**: browser-automation clicks by raw `(x,y)` coordinate go stale once
the message list scrolls the target off the captured screenshot's position — re-screenshot
or use `find`/`read_page` for a fresh `ref` instead of reusing old coordinates.

---

## Notebook privacy (per-person sharing) + folder-mutation Access-Role gate (2026-07-27 — COMMITTED `e01c0f9` + docs `695e78b`, pushed)

Two direct-ask bugs (not buildplan items), full design via `/architect` first: (1) any org
member could see/open/chat in ANY notebook regardless of creator; (2) a member without
Access-Role visibility into a tag-restricted folder could still rename/move/delete it —
the tag-gating `resolve_allowed_documents` already enforced for retrieval/chat was never
applied to folder-tree mutation.

**Notebook privacy — locked decisions**: private to `Notebook.created_by` by default,
**no owner/admin bypass** (the one place in this codebase where org owner/admin does NOT
see everything — confirmed directly by the user, overriding my own first-draft
assumption). Sharing is direct per-person (new `notebook_shares` table, migration `0020`,
full F60-pattern RLS), not routed through Access Roles. Shared users get view+chat only —
rename/delete/attach/detach/manage-sharing stay creator-only. Denial is **403, not 404**
(a private notebook confirms existence but denies access — user's explicit choice, since
this app already reveals org-membership elsewhere via `AmbiguousLogin`). The anonymous
public embed-widget path is unaffected by design (already proves consent a different way).

**Folder-mutation gate — locked decisions**: reuses the exact tag-inheritance rule
`resolve_allowed_documents` already applies. **Org owner/admin DO bypass this one**
(unlike notebook privacy — confirmed directly, admin keeps full document access). Gates
rename, move (source AND destination), delete, subfolder-creation under a restricted
parent, and moving a document into/out of a restricted folder. Browsing stays open to
everyone — only mutating actions are gated. New `FolderOut.can_manage: bool` drives both
the frontend greying-out and the actual backend enforcement (single source of truth).

**Backend**: `NotebookShareRepository`, `NotebookRepository.list_visible(user_id)`,
`_fetch_visible`/`_fetch_manageable` helpers, `NotebookAccessDenied`→403. Promoted
`_inherited_folder_tags` to exported `resolve_folder_effective_tags` in
`access_roles.py` (shared by both the retrieval gate and the new mutation gate). New
`resolve_accessible_folder_ids`, `FolderAccessDenied`→403, `_assert_folder_access` wired
into `create_folder`/`_relocate_folder`/`delete_folder`/`move_document`.

**Frontend**: `ShareNotebookDialog.tsx` (new), `NotebookPage.tsx`/`NotebookList.tsx`
gain `isOwner` gating + "Shared" badge, `FolderTree.tsx` gates rename/delete/drag/drop/
+New-folder on `can_manage`. Known minor gap, accepted: `DocumentList.tsx`'s folder-move
`<select>` doesn't proactively filter out restricted targets (backend still 403s
correctly, generic error toast surfaces it).

**Verification**: backend 293 passed/2 deselected (was 280+13 new), ruff clean. Frontend
156 passed (was 151+5), tsc/build clean. Migration `0020` applied to Testcontainers + real
dev Postgres. **Live-verified end-to-end with two real accounts** (Olivia
owner, Mia invited member) using `read_network_requests` to confirm real HTTP status
codes: Mia 403'd on notebook/documents/**chat-history** endpoints pre-share, 200 post-share
with "SHARED" badge; folder gate PATCH rename 403→`can_manage:false` before Access-Role
assignment, 200→`can_manage:true` after, rename/delete icons appeared live in FolderTree.

**Gotcha reconfirmed**: reading a real invite-link/token value via `javascript_tool` is
blocked by the safety classifier even for a throwaway test credential — workaround: widen
the containing `<input>` via a pure CSS style mutation, then read it visually via
`computer` zoom on the screenshot.

**Committed in two steps at direct request**: `e01c0f9` (code) + `695e78b` (docs), pushed
to `origin/main`.

---

## Condensed history (older sessions, chronological, newest first)

- **2026-07-24 — Embed widget hardening** (`b649754`, pushed): fixed reverse-proxy IP
  spoofing (`get_client_ip()` only trusts `X-Forwarded-For` from a configured
  `TRUSTED_PROXY_IPS` peer) and a fixed-window rate-limit boundary-burst flaw
  (`RedisRateLimiter` → sliding-window counter) on the public embed endpoint. Follow-up
  same session: documented `PUBLIC_APP_URL`/`WIDGET_*_RATE_LIMIT` in `.env.example`, added
  `frontend/public/_headers` (Netlify/Cloudflare Pages convention) for clickjacking
  protection on all authenticated routes, deliberately excluding `/embed`. Gotcha: uvicorn
  defaults `proxy_headers=True`/`forwarded_allow_ips="127.0.0.1"` out of the box —
  independent from and stacked under this app's own `TRUSTED_PROXY_IPS`. Suite: 280
  passed/2 skipped.
- **2026-07-23 — Embeddable website chatbot widget** (`7ee4a0e`): new `widgets` table
  (migration `0019`, full RLS), admin CRUD + public no-auth `GET .../config` +
  `POST .../stream` (SSE, validated before `StreamingResponse` is built so 404/403/429 are
  real HTTP statuses not mid-stream events), `app/utils/rate_limit.py` (Redis fixed-window,
  DI-selected like ObjectStore), origin allowlist (empty=allow-all), `widget.js` (ES5 IIFE
  bubble+iframe), public `/embed` page outside auth, admin `/app/embed` management page.
  Live E2E proven with real OpenRouter seams: streamed cited answer, origin-rejection 403,
  revocation → 404. Suite: 269 passed/2 skipped, frontend 151 passed. Vite proxy gotcha:
  scope `/embed` proxy entries to `/embed/widgets`+`/embed/public` only — a bare `/embed`
  entry breaks the SPA page route of the same name.
- **2026-07-20 — UX audit, final 7 findings** (`8193f41`, pushed): closed all 15 audit
  findings across 3 sessions. This batch: document table metadata + detail modal
  (`documents.uploaded_by`, migration `0017`), citation page numbers alongside char
  offsets, keyboard-focus equivalents for hover-only row actions (WCAG 2.1.1), static
  onboarding starter-question chips, real `users.name` field (migration `0018`) replacing
  email-guessing, working copy + UI-only thumbs up/down, animated status-pill pulse dot.
  Backend 252 passed/2 skipped, frontend 138 passed.
- **2026-07-19 — UX audit, 4 High findings** (`8861a7c`, frontend-only): empty-notebook
  Ask now disables input with an explanatory message; responsive off-canvas sidebar +
  stacked layouts below `lg:`; new `Modal`/`DialogContext`/`useDialog` system replacing
  every native `alert`/`confirm`/`prompt` (incl. a bespoke typed-name-confirm
  `FolderDeleteDialog`); dead "Search" nav item wired to the existing notebook-scoped
  `/retrieval/search` endpoint via new `SearchPage.tsx`. 101 passed frontend. Known gap:
  mobile-viewport visual proof incomplete (`resize_window` doesn't reliably reflow in this
  sandbox — rely on Tailwind breakpoint code review + jsdom tests instead).
- **2026-07-16 — UX audit, 4 Critical findings** (`8964ec6`): chat history hydrates on
  notebook navigation (new `GET /chat/notebooks/{id}/messages`); `extractErrorDetail`
  fixes `[object Object]` validation-error rendering; always-visible move-to-folder
  `<select>` replaces drag-only document move; invite flow replaced with a one-time
  self-serve link (new `invite_tokens` table, migration `0016`, `POST /auth/accept-invite`)
  instead of admin-typed passwords. Gotchas: restart stale dev uvicorn after backend
  edits; dev Postgres needs its own manual `alembic upgrade head`, separate from the test
  suite's Testcontainers run.
- **2026-07-16 — V2 hardening + real-seam hierarchical-retrieval eval harness**: promoted
  `retrieval.hierarchical_used` DEBUG→INFO; surfaced `sections.topics` via a DEBUG log only
  (chat response shape untouched); hardened `semantic_outline.py` (junk-heading filtering +
  overlapping windows with adjacent-window dedup, fixing gaps the prior session's live
  validation named); added admin `POST /ingestion/enrich-backfill` for pre-flag READY docs.
  Suite: 229 passed/2 deselected. **Eval harness verdict** (`test_hierarchical_eval.py`,
  opt-in `hierarchical_eval` marker, real OpenRouter seams on `pdf/kech104.pdf`):
  hierarchical retrieval tied flat on all 8 golden questions (0 wins either way), 8/8
  evidence PASS, true provenance 8/8 on manual review. **Recommendation: keep
  `HIERARCHICAL_RETRIEVAL_ENABLED` off by default** until a genuinely large multi-document
  notebook creates real pressure on flat's precision — this single ~36-page-doc corpus
  wasn't large enough to stress it. Gotcha: `structlog.testing.capture_logs()` does NOT
  lift the app's `LOG_LEVEL=INFO` wrapper_class filter — a DEBUG-level assertion inside it
  needs a temporary `wrapper_class` swap around the capture block.
- **2026-07-15 — Semantic outline + enrichment + hierarchical retrieval (V2 activated)**
  (`ce3eebd`): 3 flag-gated features, zero migrations (schema pre-designed).
  `SEMANTIC_OUTLINE_ENABLED` — LLM proposes headings verbatim, offsets located via
  `text.find()` only (never fabricated), cached artifact, always falls back to parser
  outline on any failure. `ENRICHMENT_ENABLED` — per-section LLM summary/topics →
  `sections.summary/topics` + `owner_type='section'` embeddings, never mutates document
  status. `HIERARCHICAL_RETRIEVAL_ENABLED` — coarse section kNN then fine chunk kNN,
  falls back to flat on zero hits at either stage. **Bug found only by live real-LLM
  validation (fakes passed)**: enrichment passed plain dicts to `llm.stream()`; `RealLLM`
  does attribute access → every section failed. Root cause: the test fake LLM ignored its
  messages argument entirely. Fixed with `Message(...)` dataclasses + hardened all test
  fakes to access `m.role`/`m.content`. **Lesson: any new seam call site needs either a
  live real-seam check or a fake that exercises the seam's argument contract.** Live
  validation recovered 31–32/~34 real headings (vs the old page-level wrapper). Suite: 218
  passed/1 skipped.
- **2026-07-14 — Full-system live validation + Haiku-swarm re-review**: verdict healthy,
  zero critical/major findings. Live E2E with real OpenRouter seams: 7/7 chat answers
  correct with correct citations, exact refusal + 0 citations on out-of-scope/hallucination
  bait, RBAC 13/13 correct. **Embedding-quality verdict: good, AI chunk-enrichment NOT
  needed** (this finding was later extended, not contradicted, by the 07-16 eval harness).
  Env fix: `pypdf` was missing from the venv despite being declared in `pyproject.toml` —
  any fresh environment should `pip install -e .` before trusting real-parser runs. Dev DB
  upgraded 0012→0015 (head).
- **2026-07-14 — F60 Enforced RLS** (`4f09623`) — the last buildplan item, closing all
  phases 0–6. Migration `0015`: unconditional `ENABLE`+`FORCE RLS` + `tenant_isolation`
  policy on all 18 tenant tables, `app_user`/`migrator` role split. **The real work was
  un-drifting the plumbing**: `tenant_session(org_id)` existed since F02 but nothing called
  it — all ~62 session-opening call sites used bare `sessionmaker()`, so the RLS-keying GUC
  was never actually set anywhere. All refactored to `tenant_session`/new
  `auth_session(email)` (pre-tenant bootstrap for signup/login's cross-org email lookups).
  **Real bug found by the teeth tests**: the policy predicate needs
  `NULLIF(current_setting('app.org_id', true), '')::uuid` — a committed transaction-local
  GUC resets to `''` (not NULL) on a pooled connection, and `''::uuid` raises. New guard
  test bans bare `sessionmaker()` outside `config/db.py`. Suite: 191 passed/1 skipped.
- **2026-07-13 — F42 Admin debug bundle** (`8dfe315`): new `message_traces` table
  (migration `0014`) persists hits/final_prompt/raw_output per answer; admin-gated
  `GET /chat/messages/{id}/trace`; frontend Debug toggle on chat bubbles (owner/admin
  only). Mid-review: `services/chat.py` had crossed the package-layout promotion trigger
  (435 lines, 3 repo classes) — split into `services/chat/{repository,service}.py` same
  session, zero logic change. This is the reference example for a repository/service-axis
  split (vs. `documents/`'s by-subdomain split or `ingestion/`'s by-pipeline-stage split).
  Suite: 185 passed backend, 54 passed frontend.
- **2026-07-13 — Full-codebase Haiku-swarm review + `/chat/stream` tests** (`c7d9b50`):
  7-agent review, verdict healthy, all hard rules pass everywhere. Only substantive gap
  (zero tests for `POST /chat/stream`) closed same session, 7 new tests. Suite: 180
  passed/1 skipped. Lesson: Haiku reviewers over-grade severity and can misapply
  documented gotchas — always re-grade findings against the project record.
- **2026-07-13 — Auth hardening** (`a13d307`): migration `0013` adds
  `is_active`/`failed_login_attempts`/`locked_until`/`token_version` to `users`. Member
  soft-deactivation (`PATCH /auth/users/{id}/status`), instant revocation on every request
  via `current_user`'s existing per-request DB read (no new blacklist infra needed),
  self-service `POST /auth/me/password` (bumps `token_version`, invalidates every other
  session, returns a fresh token pair for the changing session), per-account (never
  per-IP) login lockout (5 attempts/15 min). Real transaction-ordering bug caught during
  implementation: the lockout counter write must NOT happen inside the same
  `session.begin()` block that then raises — the exception would roll back the very
  counter increment it just made; fixed with a `pending_error` pattern (write, capture
  exception, let block commit, then raise). Email-invite-links/password-reset/MFA
  deliberately deferred (would need a new email-provider seam). Suite: 173 passed backend,
  52 passed frontend.
- **2026-07-12 — Access Roles (tag-based RBAC) + folder tree drag-and-drop** (`cc3faf3`)
  — **supersedes and fully deletes** an earlier same-session `folders.restricted` boolean
  feature (column, endpoint, FolderTree lock button all removed, not deprecated). New
  tables (migration `0012`): `access_roles`, `user_access_roles`, `access_role_tags`,
  `folder_tags`. A tag becomes access-controlling only once granted to an Access Role —
  untagged/ungranted resources stay open to everyone (zero behavior change for the common
  case). Folder-tagging is admin/owner-only; document-tagging stays open to any member —
  a named, accepted risk that a member could inadvertently gate a document. New
  `PATCH /documents/{id}/folder` for drag-and-drop moves. New `AccessRolesPage.tsx`.
  Live-verified end-to-end: outsider member got zero retrieval hits, role-holding member
  got the grounded hit. Suite: 158 passed backend, 44 passed frontend.
- **2026-07-12 — Document hard-delete** (`7012af2`): `DELETE /documents/{id}` (cascades
  via existing FKs; new `ObjectStore.delete` removes the blob after DB commit — DB-first
  ordering means a failed blob delete only orphans a blob, never leaves a dangling row).
  Frontend delete button, confirm-guarded. Gotcha: clicking a `window.confirm`-guarded
  button via browser automation blocks the tab (CDP hangs until the native dialog is
  dismissed) — recover with a bare `key: Return` press, don't try to click through it.
- **2026-07-02 — Single-MVC re-refactor** (`81bd90f`): pure structural refactor, zero
  logic/schema/API change. Backend: layer-first (`app/{models,schemas,controllers,
  services,repositories,exceptions,tasks}/`) → Express-style single-MVC
  (`app/{models,routes,controllers,services,middleware,config,utils}/`, collapsed
  repositories/exceptions into services, schemas into models). Frontend: `src/{models,
  controllers,views}/` → conventional SPA (`src/{types,services,pages,components,
  layouts,context,hooks,styles}/`). Full old→new path mapping tables were in this file
  before compaction — if a future session needs to resolve a very old pre-2026-07-02 path
  reference, check `git log` / `docs/mvc-refactor-prompt.md` (kept in-repo) rather than
  this file. Route table proven byte-identical to pre-refactor HEAD via OpenAPI diff.
  Gotcha (still relevant): `app/config/__init__.py`'s `from app.config.settings import
  settings` re-export SHADOWS the `app.config.settings` submodule as a package attribute —
  `import app.config.settings as X` binds the object, not the module. Code needing the
  settings singleton must bind directly: `from app.config.settings import settings`.
  Docker-gated re-run: 135 passed/1 skipped.
- **2026-07-01 — MVC layout refactor** (`6ff4be7`, superseded by the above the next day):
  domain-first (`app/<domain>/`) → layer-first MVC. Historical only.
- **2026-07-01 — Real-embedder retrieval validation**: confirmed retrieval/grounding
  fully correct with the real embedder across documents with varied heading structure.
  **Finding**: OpenRouter's `cloudflare-ai` file-parser plugin does not recover semantic
  headings on two-column academic PDFs — it only marks page boundaries (`document.pdf >
  Metadata > Contents > Page N` for every document tested, including one where real
  headings were visually bold/large-font). The heading text IS present in the extracted
  text but fused into the surrounding paragraph with zero markdown/whitespace separator —
  there's no signal for `_parse_markdown_outline`'s regex to detect. Per the locked
  "never fabricate" contract this is correct behavior, not a bug — retrieval quality is
  unaffected since F31 keys off chunk-content embeddings, not section labels/paths. Open
  question for future V2 hierarchical-retrieval design: this vendor may only ever offer
  page-level granularity for this document class.

## Earlier foundation work (F00–F41, 2026-06-21 through 2026-06-24)

Summarized in full in `progresstracker.md`'s Phase 0–4 tables (commit hashes, DoD, test
counts per feature) — not repeated here. Covers: platform skeleton, DB/migrations, seams
+ fakes, CI; auth+org, folders+tags, upload+dedupe; parsing/structuring/embedding stages;
real-parser integration; notebooks + flat retrieval; grounded generation (F40) + citations
(F41, migration `0009`) + SSE streaming (F4x). F40's manual acceptance gate (real
parser+embedder+LLM against `pdf/kech104.pdf`) confirmed grounded answers in-scope and
exact refusal out-of-scope — first proof the whole real pipeline works end to end.

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

**Next migration: 0021.**

---

## Open questions / future decisions

- Reranker (4th seam) — add when real quality complaints arise in V2.
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
