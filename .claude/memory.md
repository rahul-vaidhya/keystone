# memory.md — Session Memory

> Compressed, durable record of decisions and state. Restored at the start of every
> session, updated by the **Remember** skill at the end of every session.
> Keep it short and high-signal. Delete stale entries.

## F41 Citations (2026-06-24, this session, feature commit pending)
- **New `app/chat/models.py` + `repository.py`** (`Conversation`, `Message` — architecture's
  locked table names). Migration `0009_conversations_messages.py`. `chat/` stays flat (6
  files now, ~250-line `service.py`) — reviewed against hard rule #8 and judged still one
  cohesive request-flow pipeline, not 2+ independent responsibility groups; revisit if it
  grows further.
- **The real logic gap F41 fills**: F40's `ChatResponse.citations` was every block
  retrieval returned, regardless of whether the model actually cited it. F41 derives
  citations from the model's `[n]` markers in the answer text instead —
  `parse_citation_markers` (pure function, regex `\[(\d+)\]`, dedup, order of first
  appearance) → `resolve_citations` maps each marker to the `ContextBlock` actually sent
  at that position.
- **Drop-invalid-marker rule, decided and applied**: a marker outside `1..len(blocks)`, or
  one whose mapped chunk no longer exists by resolution time, is dropped from the result
  — logged (`chat.citations_resolved`: counts of parsed/resolved/dropped-out-of-range/
  dropped-missing-chunk), never raised, never fabricated. The literal `[n]` text stays in
  the answer string untouched; only the structured `citations` list omits it. Tested:
  `test_ask_out_of_range_marker_is_dropped_not_fabricated`.
- **Provenance round-trip, the core of F41 (this feature's equivalent of F40's refusal
  gate)**: `resolve_citations` rebuilds each `ResolvedCitation` from a FRESH
  `ingestion_service.get_chunks(ctx, chunk_ids)` read of the chunk row — the
  source-of-truth `chunks` table — rather than trusting the `ContextBlock`'s
  self-reported fields copied earlier in the same request. Tested directly:
  `test_ask_citation_provenance_round_trip_matches_stored_chunk` independently re-reads
  the chunk row via raw SQL and asserts the citation matches exactly, including that
  `stored_chunk.content[char_start:char_end] == citation.content`.
- **New narrow `ingestion.service` accessor: `get_chunks(ctx, chunk_ids) -> list[ChunkRecord]`**
  (`ingestion/service/search.py`, backed by new `ChunkRepository.get_by_ids`) — same
  precedent as F31's `search_chunks` / F30's `get_document`: one real caller
  (`chat.service.resolve_citations`), added because `chat` must reach ingestion's tables
  only through ingestion's service (hard rule #1), never by importing
  `ingestion.models`/`ingestion.repository` directly. `get_by_ids` filters `org_id`
  directly as an independent backstop (not relying on the caller having already
  org-scoped the chunk_ids) — by-id fetches are exactly where a missing filter would leak
  cross-tenant content, so it got its own direct isolation test
  (`test_get_chunks_org_id_is_an_independent_backstop`, same two-pronged
  cross-org-empty + same-org-control pattern as F31's `search_chunks` test), not just a
  happy-path check.
- **Persistence scope decision, explicit (direct instruction, not silent narrowing)**:
  every `/chat/ask` call creates a FRESH `Conversation` + a user `Message` (citations=
  null) + an assistant `Message` (citations=resolved jsonb) — `ChatRequest` does NOT
  accept a `conversation_id` to reuse, and there is no `ConversationNotFound`/cross-org
  validation for one, because there is no input to validate. Reasoning, same shape as
  F40's SSE deferral: conversation reuse only earns its place alongside multi-turn
  history-threading (using prior messages as LLM context), which F41 deliberately does
  NOT build — shipping append-to-conversation with no read/threading side yet would be
  speculative storage (hard rule #8). **The reuse path is for a future multi-turn
  feature to build, together with the threading that gives it a purpose** — don't
  reintroduce `conversation_id` reuse in isolation without that.
- **`ChatResponse` shape changed** (breaking, by design): `citations: list[ContextBlock]`
  → `citations: list[ResolvedCitation]` (`marker`, `document_id`, `chunk_id`,
  `char_start`, `char_end`, `content` — no `index`/`distance`), plus new
  `conversation_id`/`message_id` fields. F40's old "all blocks numbered 1..N" test
  (`test_ask_multiple_chunks_numbered_citations_match_blocks`) was rewritten to use a new
  test-local `_MultiCitingLLM` double (cites every block it's sent) — the existing
  `FakeLLM` only ever cites `[1]` regardless of context size, so it could never have
  exercised multi-citation resolution; this is now the standing way to test >1 citation
  without changing `FakeLLM`'s own contract again.
- **`migrations/env.py`**: uncommented/added `import app.chat.models` for ORM metadata
  registration (the placeholder comment for this was already there from F40, per the
  package-layout convention's ORM-registration rule).
- Independent code-review pass (separate subagent, given only this session's diff + the
  hard rules + the F41 DoD): zero violations against rules #1/#3/#4/#8; one cosmetic note
  (a stale "chat is stateless" docstring on `GenerationFailed`, fixed in this session).
- 99/99 suite green (1 pre-existing real-parser test deselected, matching prior sessions'
  count), ruff clean.

## F40 manual acceptance gate (2026-06-24, this session, validation only — no code changes)
- **Satisfied the outstanding DoD item from the F40 entry below**: ran the real pipeline
  end-to-end (real `RealParser` + real `RealEmbedder` + real `RealLLM`, all via the existing
  `OPENROUTER_API_KEY`, model `openai/gpt-4o-mini`) against `pdf/kech104.pdf` (NCERT Class 11
  Chemistry, "Chemical Bonding and Molecular Structure"), through `chat_service.ask` exactly
  as a real request would hit it — not a mocked/simulated call.
  - **In-scope question** ("What is the Kossel-Lewis approach to chemical bonding, and what
    is the octet rule?"): retrieval distances 0.35–0.42 (tight, genuinely relevant), real LLM
    returned a correct grounded answer citing `[1][6]`.
  - **Out-of-scope question** ("Who won the FIFA World Cup in 2022...?"): retrieval distances
    0.80–0.90 (loose, nothing actually matched), real LLM returned the exact fixed string
    `"I don't have that in the provided sources."` verbatim — no fallback to its own training
    knowledge. This is the specific behavior the gate exists to catch (a model confidently
    fabricating from pretraining when retrieval comes up empty/irrelevant) and it held.
- **Config/script-only — zero production code touched.** Verified via `git status`/`git
  diff` (clean) before and after. The only "change" was a throwaway script (outside the
  repo, in session scratchpad) that imported the already-existing `RealEmbedder`/`RealLLM`
  classes (built in F03) instead of the fakes, and mutated the in-process `settings`
  singleton's `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`LLM_MODEL` at runtime — never written to
  `.env`, never touched `backend/app/*`. Bypassed R2 (no creds configured in this dev
  environment) via a local-disk `ObjectStore` impl in the same script, mirroring the
  in-memory fakes the test suite already uses for that dependency. Confirmed afterward:
  `pytest -q -m "not real_parser"` still 95 passed / 1 deselected, fake-only, no real key
  required — CI posture unchanged.
- **Finding: the FAKE embedder cannot produce a true-positive case for this kind of manual
  test.** First attempt used `FakeEmbedder` (hash-of-text, semantically meaningless) for the
  in-scope question — it retrieved essentially random chunks (distances ~0.93–0.96, no
  separation from the out-of-scope run) and the real LLM correctly refused given that
  irrelevant context. That's a *correct* refusal, but it doesn't prove grounded-citation
  behavior works, only that refusal-on-noise works. Switching to the real embedder is what
  produced the genuine positive/negative pair (tight vs. loose distances) above. **Applies
  to any future manual retrieval/chat validation**: if the goal includes confirming a
  *correct* grounded answer (not just refusal), the embedder must be real — fakes are fine
  for refusal-only checks but will under-test groundedness.
- **Architectural note worth carrying forward**: `OPENROUTER_API_KEY` alone, with
  `OPENAI_BASE_URL` pointed at `https://openrouter.ai/api/v1` and `OPENAI_API_KEY` set to
  the same value, serves all 3 seams — parser (already known, F23), and now confirmed for
  embeddings (`RealEmbedder.embed` succeeded against `text-embedding-3-small` through
  OpenRouter) and LLM (`RealLLM.stream` succeeded against `openai/gpt-4o-mini`). One
  credential, three independently-metered vendor paths. Relevant for the eventual deploy/
  secrets story and any Phase 5+ cost-control thinking — there is no technical need for a
  separate `OPENAI_API_KEY` in this stack unless a reason to split billing/rate-limits
  between parser and embedder/LLM traffic comes up later.

## F40 Grounded generation (2026-06-24, this session, `1572fa8`)
- **New `app/chat` module, stateless** (`schemas.py`/`service.py`/`router.py`/
  `exceptions.py` only — no `models.py`/`repository.py`/`tasks.py`, no migration).
  Buildplan's F40 DoD doesn't require persistence (`messages`/`conversations` are F41/F42's
  job — F41 explicitly says "store in `messages.citations`," implying the table lands
  there); building chat tables now would have been inventing scope, not following the DoD.
- **Streaming deviation, deliberate, recorded as a buildplan amendment (not silent)**:
  buildplan.md's original F40 line said "stream over SSE." This session built
  **non-streaming** instead — `POST /chat/ask` returns a complete `ChatResponse`. Rationale
  (direct instruction): no SSE consumer exists yet (F52 chat UI isn't built), F40's
  substance (grounding discipline, retrieval wiring, retry/failure handling) is
  transport-independent, and building the harder transport before anything needs it is
  premature. **This is NOT scope-deletion** — buildplan.md now has an explicit new line,
  **F4x SSE streaming for chat**, carrying the requirement forward as committed scope.
  `chat/service.py`'s `generate_answer` is an async-generator core (`AsyncIterator[str]`)
  that F40's retry wrapper consumes to completion; F4x should only need to change the
  router (consume incrementally instead) and is justified as designing-for-a-committed-
  roadmap-item, not speculative structure — if F4x ever needs more than a router change,
  that's a signal this design call was wrong, revisit it then.
- **Retry classification, the one place this session deviated from architecture's own
  precedent on purpose**: `run_parsing_stage`/`run_embedding_stage` (F20/F22) catch broad
  `except Exception` at their seam call because that's a TERMINAL stage boundary (the
  document just goes to FAILED either way). `call_llm_with_retry` is INSIDE a retry loop,
  where a broad catch would silently retry deterministic bugs (e.g. a `KeyError` in prompt
  construction), wasting paid LLM calls and burying the real exception behind a 503 —
  rejected explicitly, by direct instruction, as the wrong precedent to reuse here.
  Resolution: a new `SeamTransientError` (`platform/seams/types.py`) is raised ONLY by
  `RealLLM` (`platform/seams/real_llm.py`'s `_classify_transient`) for OpenAI
  `APITimeoutError`/`APIConnectionError`/`RateLimitError`/`InternalServerError`, or any
  exception carrying `status_code in (429,) or status_code >= 500`. `call_llm_with_retry`
  catches ONLY `(TimeoutError, SeamTransientError)` — our own `asyncio.timeout` plus that
  one typed signal — retries up to `LLM_MAX_RETRIES` (default 2) with exponential backoff
  (`LLM_RETRY_BACKOFF_BASE_SECONDS * 2**attempt`), and raises `chat.exceptions
  .GenerationFailed` (-> 503 via a new `platform/http.py` handler) on exhaustion. Anything
  else (a bug, a validation error) propagates immediately, unretried, uncaught — keeps
  vendor detail behind the seam (hard rule #4) while keeping the retry loop honest. **If a
  future seam consumer is tempted to broad-catch inside a retry loop, this is the
  counter-example — broad catch is fine at a terminal stage boundary, not inside retries.**
- **`LLM` protocol gained a `.model` property** (`platform/seams/protocols.py`), mirroring
  `Embedder.model` — `FakeLLM.model -> "fake-llm"`, `RealLLM.model -> settings.LLM_MODEL`.
  Needed so `ChatResponse.model` and the `chat.llm_call_succeeded` log line have a real
  value to report. Small, additive, same shape as the existing `Embedder.model` precedent.
- **`FakeLLM` behavior change (breaking for its old contract, by design)**: it used to
  always cite `[1]` regardless of input. Now it's minimally context-aware — checks whether
  the latest user-turn message contains a numbered context marker (`"[1]"` substring); if
  not, streams the fixed refusal string (`FakeLLM.REFUSAL`); if so, streams the old citing
  template. This is the only way to test F40's core behavioral promise (refuse vs. ground)
  against a deterministic fake with no network — every future LLM-seam consumer benefits
  from a fake that can express both states. **Updated the one pre-existing F03 test**
  (`tests/test_seams.py::test_fake_llm_streams_grounded_cited_answer`) to send a
  context-bearing prompt explicitly, and added a sibling refusal test — this was a
  deliberate, approved contract change, not an accidental break.
- **`ContextBlock.distance: float` added to `retrieval/schemas.py`** (F31's previously
  "locked" module) — `ChunkHit.distance` was already computed by F31's SQL and silently
  dropped in `assemble_context`; this surfaces it unchanged. Justified ONLY by F40's own
  logging need (chunk_ids + distances when available) — explicitly NOT justified by "F42
  will want it later" (that's the speculative-structure pattern hard rule #8 forbids).
  Purely additive (new trailing field, nothing renamed/removed) — confirmed F31's existing
  `tests/test_retrieval.py` has no exact-equality/dict-comparison assertions on
  `ContextBlock` that this would break; full suite stayed green (95/95, 1 pre-existing
  skip) after the change, not just assumed safe.
- **Correlation id**: minted per-request in `chat/router.py` (`uuid4`), bound via
  `structlog.contextvars.bind_contextvars(correlation_id=...)`, unbound in `finally`. This
  is the FIRST place request-id/correlation-id propagation exists anywhere in the repo —
  `platform/logging.py`'s docstring had deferred it to "Phase 1," but Phase 1 shipped
  without it (confirmed via a repo-wide grep before building this: zero prior references).
  Deliberately scoped to chat's router only (no global middleware) rather than retrofitting
  request-id propagation across every existing module — if a future feature wants it
  elsewhere, copy this pattern, don't assume it's already wired in globally.
- **Logging discipline**: `chat.context_assembled` logs `notebook_id`/`num_blocks`/
  `chunk_ids`/`distances` but never the raw query text or chunk content — only
  `answer_chars`/`latency_ms`/`model` are logged from the LLM call, never the answer text
  itself. Matches the "don't log full document content or secrets" instruction.
- **Manual acceptance gate added to buildplan.md/progresstracker.md, not yet exercised**:
  F40's automated suite (95/95 green) only proves the plumbing and `FakeLLM`'s mechanical
  contract — it cannot certify that a REAL LLM actually refuses on an unanswerable question
  rather than inventing an answer, which is F40's actual product promise. A human must run
  this manually against a real notebook + real LLM before F40 is "done," not just
  green-and-reviewed. **Not done as of this entry — outstanding**, tracked explicitly so it
  isn't silently treated as complete.
- Independent code-review pass (separate subagent, given only this session's diff + the
  hard rules) found zero violations against rules #1/#3/#4/#8 and the F40 DoD.

## Maintenance: package-layout refactor (2026-06-24, this session, 4 commits)
- **Pure structural refactor of F00–F31, zero behavior/logic/schema/API/test-behavior
  change** — not a feature, requested by the reviewer to move from "flat files per module"
  to "package-per-module, split along real responsibilities." Ran as Phase 1 (inventory +
  propose, stopped for approval) → Phase 2 (execute, one module per commit) → Phase 3 (lock
  the convention for F40+).
- **Trigger rule (now locked in architecture.md):** promote a layer file to a subpackage
  only when it exceeds ~200 lines **AND** mixes 2+ genuinely independent responsibility
  groups (different tables/stages/vendor adapters). A long-but-cohesive file or a short
  file with several small ORM classes is NOT promoted — over-splitting to satisfy "one
  folder per module" is itself a violation, not a stricter reading of the convention.
- **4 modules promoted, in this order/commits:**
  1. `platform/seams.py` (444 lines) → `platform/seams/` — `types.py` (shared
     dataclasses + `SeamNotConfigured`), `protocols.py` (3 Protocols + `EMBED_DIM`),
     `fakes.py`, `real_parser.py` (RealParser + its 4 helpers — kept separate from
     `real_llm.py` per architecture.md's own language calling it "a SEPARATE adapter/vendor
     call"), `real_llm.py` (RealEmbedder/RealLLM + shared `_openai_client`), `factory.py`.
     `65845e1`.
  2. `ingestion/service.py` (404 lines) → `ingestion/service/` — `parsing.py`,
     `structuring.py` (incl. the tree-building/chunking algorithm helpers), `embedding.py`,
     `search.py` (the F31 retrieval entry point). `IngestionService` stays one class; each
     method delegates to its stage's free function — zero logic change, just relocated.
     `8a72e41`.
  3. `documents/repository.py` (225 lines, 4 independent classes/tables) →
     `documents/repository/` — `folders.py`, `tags.py` (TagRepository +
     DocumentTagRepository — the join table lives with tags, not split further),
     `documents.py`. `fadbb07`.
  4. `documents/service.py` (258 lines) → `documents/service/` — `folders.py`, `tags.py`,
     `documents.py` (upload/dedupe + the F20-F22 status-pipeline transitions +
     cross-module accessors). `f50bee4`.
- **Composition decision, explicit and deliberate (direct instruction):** `documents/
  service/` uses the SAME free-function-delegation pattern as `ingestion/service/` — NOT
  mixins. `DocumentsService` had zero instance state to begin with, so converting each
  method to a free function taking `ctx` explicitly needed no extra self-like plumbing —
  confirmation the split was clean, not forced. **One composition convention across the
  codebase, not two.** If a future split's free functions would need many self-like args
  threaded through every call, that's a signal the responsibilities aren't cleanly
  separable — the instruction was to leave that file flat rather than force a bad split
  (mixins are explicitly never the fallback).
- **Call-site audit, done by grepping every `from app.*` import in `backend/` before
  moving anything** — not just the call sites the user enumerated. Found one EXTRA real
  call site not in the original list: `scripts/inspect_document.py` (a non-production
  debugging script) imported the private `_build_sections_and_chunks` helper directly from
  `app.ingestion.service`. Per direct instruction, this was NOT re-exported from the new
  `ingestion/service/__init__.py` (don't promote a deliberately-private helper to a
  package's public API to serve one debug script) — instead the script's one import line
  was updated to `app.ingestion.service.structuring`. **If this script's import ever needs
  fixing again, that's the pattern: fix the script, don't widen the package's public
  surface.** Also found `tests/test_seams.py` imports two private `RealParser` helpers
  (`_is_negligible_text`, `_parse_markdown_outline`) directly — these WERE re-exported from
  `platform/seams/__init__.py` (added to `__all__`, since ruff F401 flagged them as unused
  otherwise) because they're an existing test call site, not something newly promoted.
- **Verification, every commit:** full suite (83 passed, 1 skipped) + `ruff check` + `ruff
  format --check` green before each commit, in that order, one module per commit (never
  batched). **After all 4 modules**, additionally span up a throwaway fresh Postgres
  container (`docker run`, not the dev-compose named volume) and ran `alembic upgrade head`
  against it from empty — confirmed all 8 migrations apply clean and all 11 expected tables
  (`organizations, users, folders, tags, documents, document_tags, sections, chunks,
  embeddings, knowledge_bases, knowledge_base_documents`) register. This was a deliberate
  check beyond "tests are green" because `migrations/env.py` imports each module's
  `models.py` for ORM-metadata side effects — confirmed unaffected since no `models.py` was
  moved this round (all stayed flat, correctly, since they're multiple small ORM classes
  with zero logic — not a trigger-rule match).
- **No circular imports introduced** by either service split (`ingestion/service/__init__.py`
  importing its own stage submodules; `documents/service/__init__.py` likewise) — confirmed
  by the suite passing, since any cycle would fail at import time.
- **Deliberately NOT touched** (recorded so it isn't relitigated as "missed"):
  `documents/models.py` (121 lines, 4 ORM classes, zero logic), `ingestion/models.py` (131
  lines, 3 ORM classes, zero logic), `documents/router.py` (108 lines, one cohesive
  `APIRouter`), `ingestion/repository.py` (130 lines, 3 cohesive repo classes, under
  threshold), `identity/*`, `knowledge/*`, `retrieval/*` (all already correct/small —
  `retrieval/` has no `models.py`/`repository.py` since it owns no table, which is the
  reference pattern, not a gap), `chat/` (empty — Phase 4 not started, convention applies
  prospectively when it's built).
- **Convention locked for F40+** in three places: `architecture.md` ("Package-layout
  convention" section, incl. the ORM metadata-registration rule — if a `models.py` is ever
  promoted, its `__init__.py` must import every model class or Alembic silently drops the
  table from autogenerate), `orchestrator.md` (hard rule #8 + the Implement/Review skill
  bullets), and `.claude/skills/review/SKILL.md` (a new Layer-2 check for both
  under-splitting and over-splitting). Reference module for "already correctly structured":
  `knowledge/` and `retrieval/`.

## Current phase
**Phase 0 COMPLETE** (F00–F04, F03+F04 = c35ee11, 27 tests, ruff clean). **Phase 1 (Identity +
Documents) COMPLETE**: F10 (`8940dd1`), F11 (`c58a5e7`), F12 (`0b44b9c`). **F50 + a slice of F51
(Phase 5 frontend) DONE and committed** (`054aa36`). **Phase 2 (Ingestion core path) COMPLETE**:
F20 parsing (`0277cfe`), F21 structuring (`5eecac5`), F22 embedding (`4598698`). **Phase 2.5
COMPLETE: F23 Real parser integration, code+docs committed `9e7f319`/`90285c2`; empirical
validation against a real PDF run and confirmed this session.** **Phase 3 COMPLETE: F30
Notebooks (`a85138e`), F31 Flat retrieval (`c194b2b`)** (see entry below). **Maintenance:
package-layout refactor complete this session** (4 commits — `65845e1`/`8a72e41`/`fadbb07`/
`f50bee4` — zero behavior change; convention locked for F40+, see entry below). No feature
progress from the refactor itself.
**Phase 4 STARTED: F40 Grounded generation (`1572fa8`)** — non-streaming `POST /chat/ask`,
stateless `app/chat` module, retrieval-only cross-module call, retry-only-on-transient LLM
seam call (see entry above for full detail). 95/95 suite green, ruff clean, independent
review clean against hard rules #1/#3/#4/#8. **Manual acceptance gate outstanding** (a
human must confirm the REAL LLM refuses, not invents, on a real unanswerable question —
not yet done).
Next: **the F40 manual acceptance gate, F41 Citations, F4x SSE streaming, or resume the
rest of F51 (folders/tags/upload UI) against the real F11/F12 backend — ask the user
which.**

## F31 Flat retrieval (2026-06-23, this session, `c194b2b`)
- **New `app/retrieval` module** (`router.py`/`service.py`/`schemas.py` only — no
  `models.py`/`repository.py`/`exceptions.py`: retrieval owns no table, so no new error
  types or SQL of its own). First real exercise of architecture.md's `retrieve()`
  pseudocode (`flat_vector` only — no hierarchical/graph/reranker/chat/LLM in scope).
- **Cross-module design, option (a) as approved**: the kNN SQL stays in
  `ingestion/repository.py` (`EmbeddingRepository.search_chunks`) since embeddings/chunks
  are ingestion's own tables (F21/F22). `retrieval.service` never imports
  `ingestion.models`/`ingestion.repository` — it calls a new, narrow
  `IngestionService.search_chunks(ctx, *, query_vector, document_ids, model, k)`, mirroring
  how `knowledge.service` already calls `documents_service.get_document`/`list_by_ids`
  instead of touching `documents` directly. New `app/ingestion/schemas.py` (`ChunkHit`) is
  ingestion's first schemas file (it previously reused `documents.schemas.DocumentOut` for
  everything) — carries the result shape across the module boundary as a Pydantic value
  object, not a leaked ORM row.
  - **Corrected a stale docstring** in `ingestion/models.py`: it previously said a future
    retrieval module "can import these ORM classes directly for read-side joins... without
    going through ingestion's service/repository" — written during F22, before this
    feature existed. That statement would have violated hard rule #1; replaced with a note
    that retrieval reaches this data only through `IngestionService.search_chunks`. **If
    this docstring is ever quoted as precedent again, it's wrong — option (a) is the
    locked design.**
- **No hardcoded over-fetch.** First draft (pre-implementation plan) fetched k=30 then
  sliced to `hits[:8]`, mirroring architecture.md's pseudocode literally. User caught this
  as unjustified without a reranker (none exists in F31) and inconsistent for `req.k > 30`.
  Fixed: `req.k` is passed straight through to the SQL `LIMIT` in `search_chunks`; no
  slicing happens in `assemble_context`. `RetrievalSearchRequest.k` is bounded
  `Field(default=8, ge=1, le=50)` at the schema level. **If a reranker is ever added (V2),
  it should be named explicitly as a fetch-then-rerank stage — don't reintroduce a silent
  over-fetch constant.**
- **`resolve_allowed_documents(ctx)`** is a module-level free function in
  `retrieval/service.py` (not a class method) — deliberately mirrors architecture.md's
  pseudocode shape, which shows it as a standalone function called inside `retrieve()`.
  MVP body is one line: `documents_service.list_documents(ctx)` with no filters → all org
  docs. This is the ONLY place V2 groups/grants permission logic needs to slot in later.
- **`assemble_context(query, hits)`** is a pure function (no DB, no seam) — unit-tested
  directly with fake `ChunkHit` lists. Produces `ContextBlock`s numbered 1..N with
  `document_id`/`chunk_id`/`char_start`/`char_end`/`content` — this is the SHAPE F40 (chat)
  will consume; citation mapping itself is F41, not built here.
- **Active-model filter** (`Embedding.model == model` in `search_chunks`'s WHERE clause) —
  required so a re-embed under a new model name (F22's upsert path) never produces a
  duplicate hit for the same chunk. Confirmed by `test_active_model_filter_excludes_other_models`.
- **Two isolation tests, deliberately not one** (direct instruction): an API-level test
  (cross-org notebook access 404s before `search_chunks` is ever reached) AND a
  repository-level test (`test_search_chunks_org_id_is_an_independent_backstop`) that calls
  `ingestion_service.search_chunks` directly with org A's `ctx` but org B's `document_id` in
  the scope list, asserting zero hits, with a same-org control proving the absence is the
  filter and not a query bug. **Rationale, worth keeping**: the API-level test can never
  reach the `search_chunks` internals, because `list_notebook_documents` already 404s
  cross-org before `search_chunks` is called — so only the repository-level test would catch
  a future accidental removal of the `WHERE org_id = :org` predicate inside
  `search_chunks` itself. Don't collapse these into one test in a future refactor.
- **Gotcha (test-writing only, not app code)**: tests that call `ingestion_service.search_chunks`
  directly (bypassing the HTTP `client` fixture) must depend on the `tenant_engine` fixture,
  not just `session_factory` — `session_factory` only gives the test its own engine bound to
  the Testcontainers URL; `IngestionService.search_chunks` opens its session via the
  **global** `app.platform.db.sessionmaker`, which is only rebound to the test container by
  `tenant_engine`. Missing it produces a `ConnectionRefusedError` against the dev DB URL, not
  an obviously-tenancy-related failure. The `client` fixture already depends on
  `tenant_engine` transitively, so HTTP-level tests never hit this; only direct
  service-layer test calls need to request it explicitly.
- **Gotcha (test-writing only)**: directly constructing `Document`/`Chunk`/`Embedding` rows
  for a hand-rolled `org_id` (not one created via `/auth/signup`) needs an explicit
  `Organization(id=org_id, ...)` row first (the FK target) — and needs `await
  session.flush()` between adding the `Organization` and adding the `Document` in the same
  transaction, since SQLAlchemy's ORM flush ordering for objects with explicit (non
  server-generated-and-awaited) PKs and no `relationship()` between the two mapped classes
  doesn't auto-detect the table-level FK dependency the way `MetaData.create_all` does.
- Verified independently via a code-review subagent against all 7 hard rules + F31 DoD:
  zero violations. 83/83 suite green, ruff clean (`ruff check .` / `ruff format --check .`,
  same pre-existing `scripts/inspect_document.py` exclusions as prior sessions).

## F30 Notebooks (2026-06-27, this session, `a85138e`)
- **New `app/knowledge` module** (`models.py`/`repository.py`/`service.py`/`router.py`/
  `schemas.py`/`exceptions.py`) — first module to populate the previously-empty `knowledge/`
  package architecture.md already reserved for "the reference join." Migration
  `0008_notebooks.py`.
- **Public terminology is "Notebook" everywhere** (schemas/service/router/tests/docs) per
  direct instruction; the underlying tables stay `knowledge_bases`/`knowledge_base_documents`
  to match the names already locked in architecture.md's data model. ORM classes are named
  `Notebook`/`NotebookDocument` (Python-facing name), mapped via `__tablename__` to the locked
  table names — this split (class name vs table name) is deliberate, not an inconsistency.
- **`knowledge_base_documents` carries `org_id` directly** (no scope-via-parent), per the
  hard-rule pattern already established by `document_tags`. Composite PK
  `(knowledge_base_id, document_id)` is itself what makes "a document in two notebooks has
  exactly one set of chunks/embeddings" hold — the join only ever adds a join row; chunks/
  embeddings key off `document_id` alone and are completely untouched by notebook membership.
- **Cross-module document-existence/ownership check, resolved via a documents.service call,
  not a repository/ORM import** — this was the one open design question from the architect
  step. A plain FK on `document_id` only proves the document exists *somewhere*, not that it
  belongs to the *same org* as the notebook (the case the tenant-isolation test needs to
  catch: attaching org B's document to org A's notebook must 404). Validating org-scoped
  existence needs a query against `documents` with `org_id` — only `documents.service` can do
  that without violating hard rule #1 ("a module calls another module only through its
  service, never its repository or tables"). Added **two new, narrow, non-speculative**
  `DocumentsService` methods, each with a real caller in `knowledge.service`:
  `get_document(ctx, document_id) -> DocumentOut` (attach-time existence/ownership check) and
  `list_by_ids(ctx, document_ids) -> list[DocumentOut]` (resolving a notebook's attached
  document ids back to full document rows for the "list documents in a notebook" endpoint).
  `get_document` is the SAME method F20's audit removed as speculative (zero callers, at the
  time) — it's back now because `knowledge.service.attach_document` is a genuine caller, not
  a re-introduction of dead code.
  - **First draft of `repository.py` got this wrong**: imported `app.documents.models.Document`
    directly into `knowledge/repository.py` to do a SQL `JOIN` for `list_documents` — caught in
    self-review as a hard-rule-#1 violation (a repository touching another module's table
    directly, even just for a read-only join) before it was committed. Replaced with
    `NotebookDocumentRepository.list_document_ids` (returns only ids, scoped to
    `knowledge_base_documents`) + the new `documents_service.list_by_ids` call from
    `knowledge.service`. **Don't reintroduce a cross-module ORM import for "just a join" — it
    violates hard rule #1 regardless of how read-only or convenient it looks; route it through
    a service method instead, even if that means adding one.**
- **Gotcha — `updated_at` with `onupdate=func.now()` + immediate `model_validate` raises
  `MissingGreenlet`**: a server-side `onupdate` only populates the Python attribute on a
  post-flush refresh (a SELECT), which the synchronous `model_validate` call can't trigger
  inside an async session. Fixed by setting `notebook.updated_at = datetime.now(UTC)`
  explicitly in `NotebookRepository.update` instead of relying on the column's `onupdate`.
  **If any future table needs an `updated_at` touched on update, set it explicitly in the
  repository — don't rely on `onupdate=func.now()` if the caller immediately reads the
  attribute back in the same request.**
- Attach/detach are idempotent via the same pattern as `DocumentTagRepository`
  (`ON CONFLICT DO NOTHING` on the composite PK for attach; no-op-if-absent for detach).
- Endpoints: `POST/GET /notebooks`, `GET/PATCH/DELETE /notebooks/{id}`,
  `POST/DELETE /notebooks/{id}/documents/{document_id}`, `GET /notebooks/{id}/documents`.
- No retrieval/vector/chunk/embedding/chat/ACL code touched — scope held exactly to F30.
- 14 new tests in `tests/test_knowledge.py` (create, update metadata, delete, list, attach +
  list documents, attach idempotency, detach idempotency, "document in two notebooks" DoD
  assertion, attach-missing-document 404, attach-to-missing-notebook 404, tenant isolation
  incl. cross-org attach denial). Full suite: 74/74 green (1 pre-existing skip), ruff clean
  (`ruff check .` / `ruff format --check .`, excluding the pre-existing non-production
  `scripts/inspect_document.py` lint findings from a prior session, untouched here).

## F23 empirical validation run (2026-06-23, this session, validation only — no code changes)
- Ran the opt-in `real_parser` integration test
  (`backend/tests/test_real_parser_integration.py`) against a real PDF (`pdf/kech104.pdf`, 36
  pages) with a real `OPENROUTER_API_KEY`. Test passed on the first run — **no fixes were
  required**, so the F23 code committed last session (`9e7f319`) needed zero changes.
- **Engine used: `cloudflare-ai`** — primary engine's output was sufficient; the
  `mistral-ocr` billed fallback was never triggered for this document.
- **Document reached `READY`** via the real parser → fake embedder path (parse → structure →
  embed all returned 200, final status `READY`, `language="en"`, `page_count=36`).
- **Heading recovery: confirmed yes, and better than the conservative pre-validation
  expectation** — 39 sections recovered as a genuine 3-level tree
  (`document.pdf → {Metadata, Contents → Page 1..36}`), not the degenerate single-root case.
  110 chunks created, all nested correctly under leaf sections.
- **Page-level provenance: confirmed does NOT survive, exactly as predicted** — every
  section's `page_start`/`page_end` is `(1, 36)` (document-wide) even though the heading
  *text* is literally `"Page 9"` etc. with correct char boundaries. `RealParser` hardcodes
  these fields; the markdown's page-boundary information shows up in heading text/char
  offsets but is never threaded into the `page_start`/`page_end` fields themselves. Confirmed
  bug-shaped gap, not a crash — left as a known limitation per F23's original scope (OCR/
  page-provenance tuning was explicitly deferred out of F23).
- **Offsets verified manually on two chunk pairs**: the `Metadata` section `(42,1128)`
  contains chunks `(42,1040)`+`(1040,1128)` — contiguous, no gaps/overlaps,
  `token_count == len(content)//4` exactly. Same contiguity held for a `Page 9` section's
  3 chunks. No offset corruption found.
- **F23 marked [x] complete** in progresstracker.md — this was the last remaining DoD item
  (real document reaches READY on real-parser output, findings confirmed empirically).

## F23 Real parser integration — built (2026-06-23, this session)
- **Provider, resolved**: OpenRouter's file-parser plugin, called directly over HTTP from
  `RealParser` in `app/platform/seams.py` (lazy-imported `httpx`) — a separate adapter/vendor
  call from `RealEmbedder`/`RealLLM`, never collapsed into one client even though both are
  OpenRouter-compatible endpoints. PDF only; DOCX explicitly deferred (future adapter branch).
- **Research correction during Architect**: the brief said `pdf-text` engine; confirmed against
  current OpenRouter docs that `pdf-text` is **deprecated and redirects to `cloudflare-ai`** —
  targeted `cloudflare-ai` directly instead of the deprecated alias. `mistral-ocr` is the
  billed OCR fallback, tried only if `cloudflare-ai`'s output is negligible
  (`PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE`, default 20 chars/page) — keeps OCR cost paid only
  on actually-scanned PDFs. Response shape: `choices[0].message.annotations[].file.content[]`
  is a list of `{type, text}` blocks (not a flat string); concatenated in order to build the
  canonical text that all char offsets are computed against (never the source PDF bytes).
- **Markdown-structure decision (this session's plan amendment, not the original brief)**: both
  engines return markdown, so heading structure is RECOVERED from `#`/`##`/`###` lines
  (`_parse_markdown_outline`), not hardcoded to flat. Each heading's range is extended to the
  next heading at the SAME-OR-SHALLOWER level (not just the next heading in the flat list) so
  a parent's range still covers its children — this matters because F21's tree-builder
  (`_build_section_nodes`) trusts whatever ranges the outline gives it; it does not recompute
  them. If a document's output has no markdown headings, outline is `[]` and F21's existing
  degenerate-outline contract (one root section) takes over — a valid per-document finding,
  not a bug. **Markdown level is always ≥1 by construction (regex is `#{1,6}`)**, so F21's
  stack-pop condition (`stack[-1][0] >= level`) stays well-defined — checked explicitly in
  review, no fix needed.
- **Two accepted F23 findings (empirical, to be measured against a real document, not yet
  run)**: (1) `language` is hardcoded `"en"` — OpenRouter's parser doesn't return detected
  language; (2) page-level provenance does NOT survive — markdown has no page-boundary
  markers, so every heading gets `page_start=1, page_end=page_count` (document-level, not a
  real per-heading span). The opt-in integration test prints both flags
  (`headings recovered: yes/no`, `page-level provenance survived: yes/no`) for whatever real
  PDF it's run against — **no real PDF has been run through it yet**, so these are documented
  expectations from the code, not confirmed findings from real data. Don't treat them as
  confirmed until the integration test has actually been run once.
- **Annotation-reuse deviation (approved by user, deliberate)**: did NOT thread OpenRouter's
  own hash-based annotation-reuse mechanism through the generic `ParsedDoc` seam type — that
  mechanism is for multi-turn chat reuse and doesn't fit a stateless one-shot `extract()` call,
  and would have broken the "same shape for fake and real" seam contract. Instead, the
  idempotency goal ("a retry doesn't re-pay OCR") is realized at the ingestion-stage level:
  `run_parsing_stage` now checks whether the parsing artifact already exists in the object
  store BEFORE calling `parser.extract`; if a prior attempt crashed after persisting the
  artifact but before the status write, the retry reuses it and skips the parser call
  entirely. This is the ONLY change to `ingestion/service.py` — structuring/chunking/embedding
  are byte-identical to F22 (verified via `git diff` in review). Also closes a latent gap for
  the FAKE parser (a crash-after-persist previously always re-parsed from scratch too).
- **Per-seam mode, implemented** (architecture.md already had this recorded as a decision from
  the docs-only insertion last session; this session is where the code actually changed):
  `SEAMS_MODE` replaced by independent `PARSER_MODE`/`EMBEDDER_MODE`/`LLM_MODE` (each
  `fake`|`real`, default `fake`) via `_resolve_mode()` in `seams.py`. All call sites
  (`.env.example`, `test_config_smoke.py`, `test_seams.py`) updated — grepped the whole repo
  for stray `SEAMS_MODE` references before finishing.
- **PDF page count read locally** via lazy-imported `pypdf` (added to the `[real]` pyproject
  extra, not core) — never trusted from the API response; also where an encrypted PDF is
  detected and rejected (`reader.is_encrypted`) before any network call is made.
- **Opt-in integration test, not a script**: `tests/test_real_parser_integration.py`, marked
  `real_parser` (registered in `pyproject.toml`) and `skipif`'d unless both
  `OPENROUTER_API_KEY` and `REAL_PDF_PATH` env vars are set — reuses the existing
  Testcontainers fixtures rather than building separate bootstrap script infra. CI explicitly
  excludes it (`pytest -q -m "not real_parser"` in `ci.yml`), belt-and-suspenders on top of the
  self-skip.
- **Verified manually (no real PDF, no API key)**: hand-built fake `httpx`/`pypdf` modules via
  `sys.modules` injection to exercise the full `RealParser.extract` flow end-to-end three ways
  — cloudflare-ai succeeds first try (1 call), cloudflare-ai negligible → mistral-ocr fallback
  succeeds (2 calls), and (in review) all failure modes confirmed to map onto the existing
  FAILED/failed_stage mechanism with nothing escaping `run_parsing_stage` uncaught.
- 63/63 fake-only suite green, ruff clean, independent code-reviewer pass found zero
  violations against the 7 hard rules and the F23 DoD (seam confinement, org_id scoping
  preserved, idempotency correctness, zero structuring/chunking/embedding changes, full
  failure-mode mapping, CI stays fake-only).

## F23 inserted between F22 and F30 (2026-06-23, this session, docs only — no code)
- **Why inserted:** the original buildplan jumped F22 → F30 with no real-parser validation step.
  Every stage built so far (F20 parsing → F21 structuring → F22 embedding) ran exclusively against
  `FakeParser`. Before building Phase 3+ (notebooks, retrieval, chat) on top of the structuring/
  chunking/embedding contract, the real parser's actual output shape needs to be checked against
  that contract — cheap to find a mismatch now, expensive once Phase 3+ depends on it. This ties
  directly to the project's **#1 design risk: ingestion quality on real documents** (everything
  downstream assumes the parser's outline/offsets are trustworthy; that assumption has never been
  tested against anything but a 2-node fixed fake outline).
- **Acceptance gate, not just a feature:** F23's DoD requires that swapping fake→real costs ZERO
  changes to structuring/chunking/embedding. If it doesn't hold, that mismatch is itself the
  finding to fix before F30 — F23 is a validation gate on the existing seam contract, not new
  pipeline functionality.
- **Seam mode resolution:** `SEAMS_MODE` (one global fake/real switch) is refined to per-seam
  switching (parser/embedder/llm independently `fake`/`real`) so the real parser can be exercised
  without needing embedder/LLM API keys too. Default stays `fake` everywhere; real is opt-in.
  Recorded as a decision in `architecture.md`'s seams section — the 3-seam rule itself is
  untouched, only how each seam's mode is chosen.
- **Open decision still pending, not resolved by this insertion**: which real parser/OCR provider
  to integrate. Whatever is chosen MUST return layout/heading structure (not just flat text) —
  F21's structuring stage depends on a real outline (heading + level + offsets), not just
  extracted text, to build a non-degenerate section tree. This was already an open question
  (see "Open questions / to decide later" below); F23 is the feature that will actually answer it,
  by testing a candidate provider's output against the contract.
- **Scope explicitly deferred out of F23** (do not build during F23): semantic enrichment (V2
  summaries/topics), OCR quality tuning, multi-provider fallback. F23 is contract validation only.

## F22 Embedding stage (2026-06-26, this session)
- **`Embedding` model lives in `app/ingestion/models.py`** alongside `Section`/`Chunk` — same
  reasoning as F21: ingestion's embedding stage is what produces these rows; no `retrieval`
  module exists yet to own the table. A future retrieval module can import the ORM class
  directly for the vector-join query in librarydocs.md, same precedent as sections/chunks.
  Migration `0007_embeddings.py`: `vector(1536)` column via `pgvector.sqlalchemy.Vector`
  (already a core dep), HNSW index (`vector_cosine_ops`), btree `(org_id, document_id,
  owner_type)`, `unique(owner_type, owner_id, model)`.
- **Idempotency = true upsert, NOT delete-then-rebuild** (unlike F21's sections/chunks). The
  embeddings table's `unique(owner_type, owner_id, model)` constraint exists specifically so
  a re-embed updates the existing row — `EmbeddingRepository.upsert_chunk_embeddings` uses
  `pg_insert(...).on_conflict_do_update(...)` on that constraint, updating `embedding`/`dim`.
  **Decision, not a gap**: deliberately different idempotency mechanism per stage — F21's
  sections/chunks have no natural per-row upsert key shape (a tree), F22's chunk→embedding is
  a 1:1 keyed relationship, so upsert is the simpler and more correct choice here.
- **`ChunkRepository.list_for_document` added back** (F21 removed an unused
  `list_for_document` on both Section/Chunk repos as speculative). This time it has a real
  caller (`run_embedding_stage` needs the document's chunks to embed) — not speculative.
- **One batched `embedder.embed(texts)` call per document** — the seam already takes a list,
  no manual batching added (chunk counts are small, ~1000-char windows).
- **Status pair `begin_embedding`/`complete_embedding`** added to `DocumentRepository` +
  `DocumentsService`, exact same shape as F20/F21's pairs: eligible to (re)start only if
  status is `EMBEDDING` (the state F21 leaves a doc in, or a crashed/resumed run) or `FAILED`
  with `failed_stage == EMBEDDING`; `READY` is left as-is (idempotent no-op, matches F20/F21).
- **`POST /ingestion/documents/{id}/embed`** — same thin router shape as `/parse`/`/structure`.
- **Embedder seam call happens outside any DB transaction** (read chunks in one short-lived
  session, call `embedder.embed`, then a separate transaction for the upsert) — mirrors F20's
  parsing stage not holding a transaction open across the external `Parser.extract` call.
- 4 new integration tests in `tests/test_ingestion.py`: successful embed (status→READY,
  one embedding row per chunk, `model`/`dim`/vector-length provenance asserted), failure path
  (failing fake embedder → FAILED + failed_stage=EMBEDDING), idempotent re-run (second call is
  a no-op since status is already READY — same idempotency-test shape as F20/F21), tenant
  isolation. Full suite: 59/59 green, ruff clean. No unused code/single-caller issues found on
  audit — every new repository/service method has a real caller; all in line with F20/F21's
  established patterns.

## F21 Structuring stage (2026-06-25, this session, 5eecac5)
- **New tables owned by `ingestion`, not a new module**: `app/ingestion/models.py`
  (`Section`, `Chunk`) + `app/ingestion/repository.py` (`SectionRepository`/
  `ChunkRepository`, each just `delete_for_document` + `bulk_create`). Migration
  `0006_sections_chunks.py`. Decision: ingestion produces this structural data and no
  other module exists yet that needs to own it; a future retrieval module (F31) can import
  these ORM classes directly for joins (same pattern `document_tags` already uses against
  `documents`) without violating the module-boundary rule, since that's not calling
  ingestion's service/repository methods.
- **Idempotency = delete-then-rebuild in one transaction, not row-level upsert.** On a
  (re)run, `run_structuring_stage` deletes the document's existing sections+chunks then
  inserts freshly-built ones, inside one `ingestion`-owned transaction — simpler than
  upserting a parent/child tree with stable IDs across reruns, and equally duplicate-free.
  `chunk_id` is still a deterministic `sha256(document_id|ordinal|content)` hash per
  codestandards ("Ingestion correctness") even though this code path doesn't rely on it for
  upsert-matching — it's there so a future move to true upserts needs no migration.
  Section ids are plain `uuid.uuid4()` (no determinism needed since the whole subtree is
  rebuilt together every time).
- **Tree-building algorithm**: `_build_section_nodes` in `app/ingestion/service.py` turns
  the parser's flat outline (`heading`, `level`, offsets — document order) into a nested
  tree via a stack keyed on `level`: deeper level → child of stack top; shallower/equal →
  pop until a lower-level parent is found. **Degenerate-outline contract** (architecture.md):
  empty outline → ONE root section (`heading=None`, `char_start=0`, `char_end=len(text)`),
  itself a leaf, so it still gets chunked — the invariant "every chunk has a `section_id`"
  holds by construction in both the headed and headingless case.
  - **Gotcha if this is ever revisited**: the algorithm doesn't validate that children's
    char ranges actually cover their parent's full range — it just trusts the outline.
    Chunking happens only on LEAF sections precisely to dodge the double-coverage problem
    this would otherwise cause; if a future parser ever emits a heading tree with real gaps
    between a parent's range and its children's, that gap's text is silently never chunked.
    Not handled — no real parser exists yet to produce that shape (FakeParser/F20 are both
    flat two-heading outlines).
- **Chunking**: leaf sections only, ~1000-char windows breaking on the nearest preceding
  space (`_split_into_windows`), `token_count = len(content) // 4` (a rough heuristic — no
  tokenizer dependency added; revisit if real chunk-size accuracy ever matters for cost/
  context-window tuning). `ordinal` is document-wide (not reset per section), increasing in
  document order, for stable future citation ordering.
- **`documents.service.get_parse_artifact_key(ctx, document_id) -> str`**: a narrow accessor
  added so `ingestion.service` can read the F20-written `metadata_["parse_artifact_key"]`
  without exposing the internal `metadata` column on the public `DocumentOut` HTTP response
  shape. Single caller today (ingestion's structuring stage) — deliberate, not a leftover.
- **`begin_structuring`/`complete_structuring`** added to `DocumentRepository` +
  `DocumentsService`, exactly mirroring F20's `begin_parsing`/`complete_parsing` shape:
  eligible to (re)start structuring only if status is `STRUCTURING` (the state F20 leaves a
  doc in, or a crashed-and-resumed run) or `FAILED` with `failed_stage == STRUCTURING`;
  anything `EMBEDDING`+ returns unchanged (idempotent no-op).
- **Audited for unused code**: removed `SectionRepository.list_for_document` and
  `ChunkRepository.list_for_document` before committing — written speculatively, ended up
  with zero callers (tests query `Section`/`Chunk` via a raw `select(...)` instead). Same
  audit habit as F20's removed speculative `DocumentsService.get_document`.
- 6 new integration tests in `tests/test_ingestion.py`: successful structuring (status →
  EMBEDDING), idempotent re-run (no duplicates, identical response), failure path (artifact
  missing/corrupt → FAILED + failed_stage=STRUCTURING), tenant isolation, sections/chunks
  shape assertions (every chunk's `section_id` in the document's section set, valid
  `char_start < char_end`), and the degenerate-outline case (one root section, all chunks
  attached to it). Full suite: 55/55 green, ruff clean.

## F20 Parsing stage (2026-06-24, this session, 0277cfe)
- **New `app/ingestion` module**: `service.py` + `router.py` only — deliberately **no**
  `repository.py` (ingestion owns no table of its own; every `documents` row mutation goes
  through `documents_service`, never `documents.repository` directly — the module-boundary
  hard rule), **no** `schemas.py` (reuses `documents.schemas.DocumentOut`), and **no**
  `tasks.py`/arq wiring — nothing enqueues a parse job yet (no caller), so building one now
  would be dead code. Wired the manual-trigger endpoint into `main.py`; production
  auto-dispatch (enqueue-on-upload, or F21 chaining a follow-on job) is left for whichever
  future feature actually needs it.
- **Zero migration needed.** F12 already added `page_count/language/status/failed_stage/
  error_detail/metadata` to `documents` — F20 only adds behavior, not schema.
- **New `DocumentRepository` methods** (`begin_parsing`/`complete_parsing`/`mark_failed`) and
  matching `DocumentsService` methods (`begin_parsing`/`complete_parsing`/`fail_stage`) — F20's
  ingestion module calls these instead of touching the `documents` table itself.
- **Idempotency/resumability lives in `DocumentRepository.begin_parsing`**: a document is
  eligible to (re)start parsing only if its status is `UPLOADED`, already `PARSING` (crashed
  before the artifact was persisted — nothing to resume, just re-parse), or `FAILED` with
  `failed_stage == PARSING` (retry). Anything already past parsing (`STRUCTURING`+) comes back
  unchanged with no seam/object-store calls — `IngestionService.run_parsing_stage` checks
  `document.status != PARSING` after calling `begin_parsing` and returns early if so.
- **`ObjectStore` Protocol gained a `get(key) -> bytes` method** (alongside the existing
  `put`) — F12 only ever wrote blobs; F20 is the first feature that needs to read one back
  to hand to the `Parser` seam. Added to `R2ObjectStore` too (one more `asyncio.to_thread`
  around the boto3 call).
- **Parser artifact format** (persisted to `org/{org_id}/doc/{document_id}/artifacts/
  parsing.json` via new `storage.build_artifact_key`): JSON `{text, language, page_count,
  outline: [{heading, level, char_start, char_end, page_start, page_end}, ...]}` — directly
  serializes `ParsedDoc`/`OutlineNode` from the seam. This is the contract F21 structuring
  reads from; do not change its shape without checking F21's consumer.
- **Failure handling**: `run_parsing_stage` wraps the object-store `get`/`put` + `parser.extract`
  calls in one `try/except Exception`, logs via structlog (`ingestion.parsing_failed`), then
  calls `documents_service.fail_stage(...)` — it does NOT re-raise, so the HTTP endpoint
  returns 200 with the document's `FAILED` state in the body rather than a 500. This matches
  codestandards "on failure: set status=failed, never swallow" (logged + persisted, not raised).
- **Gotcha — test object-store fixture was silently broken for round-tripping**: the existing
  `tests/test_documents.py` `client` fixture did
  `app.dependency_overrides[get_object_store] = lambda: _InMemoryObjectStore()` — a **new**
  empty store on every dependency resolution, since FastAPI calls the override fresh per
  request. F12's tests never noticed because dedupe is checked via the DB checksum, not the
  store. F20 needs to `put` (upload) then `get` (parse) the *same* blob across two separate
  HTTP requests, which only works with one shared instance — fixed by hoisting `store =
  _InMemoryObjectStore()` outside the lambda in that fixture, and using the same pattern in
  the new `tests/test_ingestion.py`. **Any future test that exercises object-store
  round-tripping across requests must use this hoisted-instance pattern, not a fresh lambda.**
- 4 new integration tests in `tests/test_ingestion.py` (successful parse incl. artifact
  content check, parser failure via a `get_parser` dependency override, idempotent re-run
  returns an identical response, cross-org parse attempt 404s). Full suite: 49/49 green,
  ruff clean. Audited for unused code: removed a speculative `DocumentsService.get_document`
  that had no caller — kept the audit trail here so it isn't silently re-added later.

## F12 Upload + checksum dedupe (2026-06-24, this session, 0b44b9c)
- **ALTERed the F11 `documents` anchor table** (migration `0005_document_upload_dedupe.py`) exactly
  per the F11 entry's pre-recorded plan: `storage_key/checksum/mime_type/byte_size/page_count/
  language/status/failed_stage/error_detail/metadata`, `unique(org_id, checksum)`. No new table.
- **`metadata` Python attribute is named `metadata_`** (mapped to the `"metadata"` DB column) —
  `metadata` is reserved on SQLAlchemy's `DeclarativeBase` (collides with `Base.metadata`).
- **New `app/documents/status.py`**: the authoritative `DocumentStatus` `StrEnum`
  (`UPLOADED|PARSING|STRUCTURING|EMBEDDING|READY|FAILED`) architecture.md names as the single
  source of truth — built in full now even though F12 only uses `UPLOADED`, since the enum itself
  (not its later transitions) is the locked schema decision.
- **New `app/platform/storage.py`**: `ObjectStore` Protocol + `R2ObjectStore` (lazy `import boto3`
  inside `__init__`, mirrors the seam adapters' lazy-import pattern) + `get_object_store()` FastAPI
  dependency factory + `build_storage_key(org_id, document_id, filename)` →
  `org/{org_id}/doc/{document_id}/source{ext}` (librarydocs.md convention).
  **This is deliberately NOT a 4th seam** — architecture.md says only Parser/Embedder/LLM are
  seams; the object store is called directly. Testability comes from plain FastAPI
  `app.dependency_overrides[get_object_store]`, not a `SEAMS_MODE`-style fake/real switch.
- **Added `boto3` and `python-multipart` as core `pyproject.toml` dependencies** (not under the
  `[real]` extra like the OpenAI seam) — unlike the seams, the object store has no fake/real mode;
  production always needs it, and tests override the FastAPI dependency instead of swapping a
  config flag. `python-multipart` is required by FastAPI for `UploadFile`/`Form` parsing.
- **Dedupe is check-then-act, not concurrency-safe** (`DocumentsService.upload_document`): looks up
  `get_by_checksum` before inserting, inside one transaction. The `unique(org_id, checksum)`
  constraint exists as a backstop, but a true concurrent double-upload of the same byte-identical
  file in the same org could still raise `IntegrityError` on commit instead of returning the
  existing row — **not handled** (no retry/catch). **Minor, deliberately left**: the DoD only
  requires sequential re-upload to dedupe, and this is a narrow race window; revisit if it's ever
  observed in practice rather than building speculative concurrency handling now.
- **Object-store-write-before-DB-commit ordering**: `upload_document` flushes the row (to get its
  `id` for the storage key) but does NOT commit until after `object_store.put()` succeeds — if the
  put fails, the whole transaction (including the row) rolls back, so there's never a DB row
  pointing at a blob that was never written. (The reverse leak — a blob written but the transaction
  then failing for an unrelated reason — is accepted; same class of gap the orphan sweep design in
  librarydocs.md's "Object storage" section exists to catch later.)
- **Upload response status code**: `201` on first upload, `200` when the checksum already existed
  (dedupe hit) — the router returns a `JSONResponse` directly rather than using `response_model`
  because the status code is data-dependent.
- 4 new integration tests in `tests/test_documents.py` (create + `status=UPLOADED`, re-upload
  returns existing doc, same-checksum-different-orgs not deduped, missing-folder 404), using a
  `_InMemoryObjectStore` test double registered via `app.dependency_overrides[get_object_store]`.
  Full suite: 45/45 green, ruff clean.

## F11 Folders + tags (2026-06-23, this session, c58a5e7)
- **New `app/documents` module** (first module besides `identity`): `models.py` (`Folder`,
  `Tag`, `Document`, `DocumentTag`), `repository.py`, `service.py`, `router.py`,
  `schemas.py`, `exceptions.py`. Migration `0004_folders_tags.py` (Revises `0003`).
- **`documents` table is deliberately minimal in this migration** — only
  `id/org_id/folder_id/title/created_at`. It exists now only so `document_tags` has
  something to FK to (folders/tags need a document to attach to for the DoD's "tag a
  document"). **F12 ALTERs this same table** to add `storage_key/checksum/mime_type/
  byte_size/page_count/language/status/failed_stage/error_detail/metadata` — F12 does
  **not** get a new table. Don't recreate `documents` in F12's migration.
  - **Why:** buildplan sequences F11 before F12, but F11's DoD ("tag a document") needs a
    document row to exist. Building the full upload/dedupe columns now would be doing F12's
    job out of order; building nothing would leave document_tags with no FK target. The
    minimal-anchor-table-now / ALTER-later split is the smallest move that respects both
    constraints, mirroring the project's existing "structural now, semantic later" pattern.
- **Folders**: self-referencing tree, `path` materialized column (e.g. `'HR/Policies'`),
  built by reading the parent's `path` at create time (`f"{parent.path}/{name}"`, or just
  `name` for a root folder). `ON DELETE CASCADE` on `parent_id` (delete cascades to
  subtree) and on `documents.folder_id` it's `SET NULL` (folder is "not a permission
  boundary" per architecture.md — deleting a folder must not delete its documents).
- **Scope decision — no folder rename/move endpoint**: buildplan's one-line feature
  description says "CRUD for the folder tree," but the actual DoD line only requires
  "create nested folders; tag a document; list by folder/tag." Implemented Create/Read/
  Delete for folders and tags; deliberately **did not** build folder rename/move, because
  a move requires rewriting the materialized `path` of every descendant (a cycle-detection
  + bulk-update routine) that the DoD doesn't exercise and that risked introducing
  untested bugs. **If a future feature needs folder move, build it then** — this was a
  scope call, not an oversight; noted here so it isn't silently re-litigated as "missing
  CRUD."
- **Tag attach/detach is idempotent**: `attach` uses `INSERT ... ON CONFLICT DO NOTHING`
  (Postgres dialect insert) on `(document_id, tag_id)`; `detach` is a no-op if the row
  doesn't exist. `create_tag` is get-or-create by `(org_id, name)` rather than erroring on
  duplicate — there's a unique constraint on `(org_id, name)` so this avoids a 409 for the
  common case of re-tagging with an existing tag name.
- **Gotcha — cross-test-file data collisions in the shared Testcontainers DB**: `pg_url` is
  a `scope="session"` fixture, so **one Postgres container is shared across every test file
  in the run**, and `AuthRepository.email_exists_globally` checks across the WHOLE
  database, not per-test. `test_documents.py`'s first draft reused emails already used in
  `test_auth.py` (`owner2@test.com` through `owner5@test.com`) and got `409 Conflict` on
  signup. Fixed by prefixing all emails in `test_documents.py` with `docs-`. **Any new test
  file that signs up users must use an email prefix/namespace unique to that file** — this
  is a standing constraint of the test harness, not a one-off bug.
- Exception handlers added to the existing `app/platform/http.py` (not a new file) for
  `FolderNotFound`/`TagNotFound`/`DocumentNotFound` (404) and the `DocumentsError` base
  (400) — same pattern as identity's handlers in the same file.
- 7 new integration tests in `tests/test_documents.py` (nested folder creation + path
  correctness, missing-parent 404, folder delete, tag-a-document + list-by-tag + detach,
  list-by-folder, tag-missing-document 404, two-org tenant isolation on folders/tags).
  Full suite: 41/41 green, ruff clean. No unused code or single-caller-abstraction issues
  found on audit — every repository/service method has a real caller.

## `/context/docs` removed (2026-06-23, this session)
- The unplanned/undecided `GET /context/docs` endpoint (see prior entry below) was **deleted**,
  not formalized — decision: too risky to ship (served `.claude/`/`CLAUDE.md` to any authenticated
  user across all orgs, not org-scoped).
- **Why:** direct senior instruction to remove it before committing F10/F50/F51, rather than
  carry an undecided cross-tenant-readable endpoint into the committed history.
- **Removed:** `backend/app/platform/context_docs.py`, the router import/mount in `main.py`,
  `CONTEXT_DOCS_ROOT` from `platform/config.py`, `test_context_docs_list` from `test_auth.py`;
  frontend `DocsPage.tsx` + `MainPanels.tsx`, `contextApi`/`DocEntry`/`DocContent` from
  `lib/api.ts`, the `/app/docs` route in `App.tsx`, the `Library` nav item in `Sidebar.tsx`, the
  `/context` Vite proxy entry, and the now-unused `react-markdown` dependency (package.json +
  regenerated package-lock.json). Auth UI and the plain app shell (Home, Users, AppShell,
  Sidebar) were kept as-is.
- **Verified:** `pytest` 34/34 green (Testcontainers Postgres, Docker Desktop had to be started
  first), `ruff check` clean, frontend `tsc -b` clean with no dangling references.
- **Committed as two commits**: `8940dd1` "F10: auth backend" (identity/*, migration 0003, auth
  tests, pyproject auth deps, main.py wiring, docker-compose port fix, the context_docs deletion
  bits that live in those same files) and `054aa36` "F50/F51 (partial): frontend auth UI + app
  shell, built ahead of sequence per direction" (Vite scaffold + auth UI, with the docs-viewer
  already stripped out before staging).
- `buildplan.md`'s "Unplanned additions" entry for this endpoint is marked RESOLVED: deleted.

## Re-baseline correction (2026-06-22, this session — docs only, no code)
- **Why this was needed:** `memory.md`/`progresstracker.md` still said "Next: F10" while F10
  (full auth backend) and F50 + an auth-adjacent slice of F51 (frontend app shell + auth UI) were
  already built, uncommitted, on disk. Re-baselined both files against actual repo state, not
  against the stale plan.
- **F10 DONE:** `app/identity/{router,service,repository,models,schemas,deps,tokens,passwords,
  constants,exceptions}.py` + migration `0003_auth_password_hash` + `tests/test_auth.py` (217
  lines). Signup creates org+owner; login is multi-org aware; refresh/logout/`/me`/invite/list
  users/patch role all present.
- **F50 DONE + F51 PARTIAL, built OUT OF SEQUENCE per direct senior instruction** (ahead of Phase
  2–4): Vite React scaffold (`frontend/{package.json,vite.config.ts,...}`), `App.tsx`/
  `ProtectedRoute`/`lib/auth.tsx`/`lib/api.ts`, `AppShell`/`Sidebar`/`HomePage` (F50), plus
  `LoginPage`/`SignupPage` wired to the real F10 endpoints and `UsersPage.tsx` (org user/role
  management) + a `DocsPage.tsx` placeholder (auth-adjacent slice of F51). Folders/tags/upload UI
  itself is **not started** — don't resume it until F11/F12 land on the backend.
- **F03/F04 status corrected:** were briefly suspected stale/skipped during this re-baseline, but
  verified still fully intact and unchanged since `c35ee11` (`git diff c35ee11 HEAD` empty for
  `seams.py`/`ci.yml`) — confirmed with the user, no actual gap. Don't re-litigate this.
- **`GET /context/docs`** — was unplanned/undecided as of the re-baseline; resolved and deleted
  this session, see the entry above.

## Phase 0 build decisions (F03 seams + F04 CI, 2026-06-22, c35ee11)
- **All 3 seams live in ONE flat module `platform/seams.py`** (matches the flat platform/ layout —
  config.py, db.py, etc.), not a package. Holds: `Parser`/`Embedder`/`LLM` `@runtime_checkable`
  Protocols; shared frozen-dataclass types `ParsedDoc`/`OutlineNode`/`Message`; fakes; real adapters;
  the factory; `SeamNotConfigured`.
- **Fakes are the product default everywhere** (`SEAMS_MODE=fake`): `FakeEmbedder` = deterministic
  unit-norm vector seeded from `int(sha256(text))` → reproducible retrieval asserts; `FakeLLM` streams
  a templated grounded answer citing `[1]`; `FakeParser` returns fixed text + a 2-node outline with
  real char offsets (so F21 structuring can run with no PDF).
- **`Embedder` Protocol exposes `model` + `dim` properties** (not just `embed`) — `model` is stamped
  onto `embeddings.model`, the retrieval filter that stops duplicate hits after a re-embed. `EMBED_DIM
  = 1536` constant ties fake + real to the `vector(1536)` column. `LLM.stream` is declared as a plain
  `def -> AsyncIterator[str]` (async-generator-compatible), matching `async def ... yield` impls.
- **Real adapters are behind the seam and config-gated, NOT prematurely committing vendors:**
  `RealEmbedder`/`RealLLM` target an OpenAI-compatible API (defaults `text-embedding-3-small` /
  `gpt-4o-mini`, both in config, swappable), **lazy-import `openai`** inside a `_openai_client()` helper
  and raise `SeamNotConfigured` if key/SDK missing → the fake-only suite needs neither. `openai` is an
  **optional `[real]` extra** in pyproject (NOT in `[dev]`/default), so CI/tests install nothing extra.
  `RealParser` is a **Phase-2 (F20) stub** that raises `SeamNotConfigured` — the OCR vendor is a locked
  deferral, so building a "real" parser now would violate that decision (noted as the one Minor).
- **Factory** `get_parser/get_embedder/get_llm()` switches on `settings.SEAMS_MODE` (`fake`|`real`),
  rejects unknown modes with `SeamNotConfigured`. Features inject the returned object (DI) so tests pass
  a fake. No seam is wired into a feature yet — first consumer is ingestion (Phase 2).
- **F04 CI** = `.github/workflows/ci.yml` (first workflow in the repo): on push + PR, ubuntu-latest
  (ships Docker so Testcontainers actually runs, no skip), `working-directory: backend`, `pip install
  -e .[dev]`, `ruff check . && ruff format --check .`, then `pytest -q`. `TESTCONTAINERS_RYUK_DISABLED=
  true` set as a job env (carried from the F02 gotcha). Seams stay on fakes → no API keys in CI.
- **F03 tests are pure unit** (`tests/test_seams.py`, 12 tests): determinism, vector width == dim,
  protocol conformance (incl. real adapters), factory switching, unknown-mode rejection, and
  real-adapter-fails-loudly (`RealEmbedder.embed` w/o key, `RealParser.extract`). No DB, no keys.
- **Pre-existing Minor (not introduced here):** a `StarletteDeprecationWarning` (httpx vs testclient)
  surfaces in the suite — unrelated to F03/F04, left for a deps-hygiene pass.

## Phase 0 build decisions (F00/F01, 2026-06-22)
- **Baseline migration = minimum**: `vector`+`citext` extensions + `organizations`+`users` only.
  Feature tables land with their features (Phases 1–2), not in a giant dead baseline.
- **ORM models per-module** (`identity/models.py`) on one shared `Base` in `platform/db.py`;
  Alembic `target_metadata = Base.metadata`, env.py imports each module's models for side effects.
- **Async stack**: `postgresql+asyncpg` DSN; async Alembic env (`async_engine_from_config` +
  `connection.run_sync`). UUID PKs default `gen_random_uuid()` (core in pg16, no pgcrypto needed).
- **Seam adapters NOT built yet** (deferred to F03 per "stop after F01"); `SEAMS_MODE=fake` config
  field exists. `tenant_session` + base-repo scoping built in F02 (0c8bd12). `frontend/` is a
  README-only stub until F50.
- **Tests**: pure smoke tests run anywhere; DB smoke uses a Testcontainers pgvector container +
  the REAL migration (no mocked DB). Layout: `backend/` (pyproject, hatchling pkg=`app`, pytest
  `pythonpath=["."]`), entrypoints `backend/main.py` + `backend/worker.py`.

## Phase 0 build decisions (F02, 2026-06-22, 0c8bd12)
- **`tenant_session(org_id)`** in `platform/db.py` (request + worker); base-repo `org_id` filter in
  `platform/repository.py` (`BaseRepository[ModelT]._scoped()`, PEP-695 generic); `TenantContext` in
  `platform/context.py`; first concrete scoped repo = `identity/repository.py:UserRepository` (F10 extends).
- **Migration 0002 is flag-gated at apply time**: `upgrade()`/`downgrade()` read `settings.RLS_ENABLED`
  and **return early when OFF** (MVP default) → the tenant_isolation policies + app_user/migrator role
  split + FORCE RLS are WRITTEN but inert. F60 flips the flag and ships the real enabling migration.
  Pattern: each future tenant table adds its policy here, keyed on `org_id` (on `id` for `organizations`).
- **NO feature tables created in F02** (document_tags/knowledge_base_documents/messages/message_traces
  don't exist yet — they land with their features, locked decision). The org_id-everywhere rule is
  honored via a metadata-guard test (`test_metadata_smoke.py`) + the 0002 RLS pattern, not by building
  Phase 1/2/4 tables now.
- **Testing tenant_session against the container**: db.py builds its engine/sessionmaker at import
  against the dev URL, so a conftest `tenant_engine` fixture **rebinds `app.platform.db.engine` +
  `.sessionmaker`** to the Testcontainers URL (restored after) so the REAL helper is exercised. DoD
  isolation test seeds both orgs unscoped, reads via the scoped repo, and a control unscoped `select`
  proves both orgs' rows coexist (so it's the filter isolating, not absent data). 15 tests green.

## Foundation-review resolutions (2026-06-21 — applied to context docs, no code)
- **Tenancy split (the key call):** *Schema + plumbing done NOW; enforced RLS + restricted DB role
  DEFERRED to Phase 6 hardening, gated by `RLS_ENABLED` (default OFF in dev/test). App-level `org_id`
  scoping is ALWAYS on.* Resolves C2/C3.
  - Every tenant-scoped table carries `org_id` — incl. join/child tables (`document_tags`,
    `knowledge_base_documents`, `messages`, `message_traces`). No scope-via-parent. (C1)
  - One `tenant_session(org_id)` helper, `set_config('app.org_id', :org, true)` (transaction-local,
    no pooled-connection leak — see gotcha below re: why NOT `SET LOCAL`), used by BOTH
    requests AND arq workers (worker reads org_id from the job payload). (C3)
  - RLS predicate: `organizations` keys on `id`; others on `org_id`; both use `current_setting(
    'app.org_id', true)` (missing_ok → unset GUC = no rows). `app_user` vs `migrator` role split,
    `FORCE RLS` — all Phase 6 (F60). (L1)
- **One authoritative status enum** in `documents/status.py`: `UPLOADED→PARSING→STRUCTURING→
  EMBEDDING→READY (+FAILED)`. Killed the stray `parsed` state. (M1)
- **F42 debug bundle persisted** to new `message_traces` table (admin-read-only), not recomputed. (M3)
- **Deletes:** DB children via `ON DELETE CASCADE` in-tx; object-store blobs via idempotent
  delete job + periodic orphan sweep. No "same DB tx" for blobs. (M4)
- **Retrieval** filters `model = :active_model` (no duplicate hits on re-embed). Filtered-ANN: exact
  KNN for small scope, HNSW+raised ef_search for large; recall tuning = V2 revisit. (M2, L6)
- **Auth:** roles `owner|admin|member` (creator→owner, invite→member); session = short-lived JWT
  access + httpOnly refresh cookie. (M5)
- **Misc:** LLM seam → `async def stream`; `ParsedDoc` gains `language` (Parser returns it);
  degenerate-outline → one root section spanning full range so every chunk has a `section_id`. (L2/L3/L5)

## The embedding-dimension asterisk (only exception to the additive V2/V3 promise)
SAME-dimension model swap is free (still `vector(1536)`, new rows under the new model name). A
DIFFERENT-dimension model requires a MIGRATION (separate vector(N) column/table per dim) — a single
fixed-width vector column cannot hold mixed dimensions. (M2)

## Locked decisions (do not relitigate without explicit reason)
- **Backend = Python** (FastAPI + async workers). The product's hard parts (ingestion, OCR,
  embeddings, RAG) live in Python's ecosystem. Node/Next.js would force the hardest work into
  the weakest tooling.
- **Frontend = Vite React SPA**, thin/presentational only. Next.js reserved for a *future*
  public marketing site + embeddable bot, never the app backbone.
- **Storage = Postgres + pgvector**, one database. Rejected MongoDB: our hot query is a vector
  search joined with relational filters; tenancy needs RLS; vectors + chunks must delete atomically.
- **Shape = modular monolith** (one deploy, hard module seams). Rejected microservices (premature).
- **Seams = exactly 3** (parser, embedder, llm) — the ones we fake in tests and will swap.
  pgvector/object-store/queue are called directly. No Protocol hierarchy, no generic DB abstraction.
- **Jobs = arq** (async, Redis-backed). Rejected Celery (heavy) for now.
- **Permissions = deferred.** MVP retrieval calls `resolve_allowed_documents(ctx)` (a function
  called INSIDE `retrieve()`), which returns all org docs in MVP. Groups/grants are designed-for but
  NOT built; the forward hook is that function, not a table or a caller-supplied parameter.
- **Eval = manual** `golden_questions.md` checklist before releases. No ragas/framework in MVP.
- **No generated TS SDK.** One hand-written typed fetch wrapper in the SPA.
- **Structural-vs-semantic split** is the spine: capture section tree / offsets / breadcrumbs now
  (free, from the parser); summaries / topics / entities later (LLM calls, flag-gated, backfillable).

## Schema future-proofing (so V2/V3 are additive)
- `sections` self-referencing tree (parent_section_id, path, offsets) — built in MVP, structural fields only.
- `chunks.section_id` links chunks into the tree now (unused by MVP retrieval).
- `embeddings` is **polymorphic** (`owner_type` chunk|section|document). MVP inserts only `chunk` rows;
  V2 inserts `section`/`document` rows into the SAME table — no migration.
- KG tables (`entities`/`mentions`/`relationships`) are designed but **created in V3**; they need only
  the provenance (chunk_id + char offsets) already stored in MVP.

## Open questions / to decide later
- Which managed parser/OCR vendor for scanned PDFs (decide in Phase 2).
- Default LLM + embedding model choice (keep behind the seam; mini-class model + text-embedding-3-small).
- When to add reranker (4th seam) — trigger is real quality complaints in V2.
- Infra providers chosen: DB = Neon (managed Postgres+pgvector, raw DATABASE_URL);
  object store = Cloudflare R2 (S3 client); auth = self-built (argon2 hash + JWT +
  current_user), NOT Supabase Auth. No database MCP in Claude Code — Alembic + repositories
  are the only DB interface (avoids bypassing the repository/migration rules).

## Gotchas learned
- **Testcontainers + Docker Desktop/Windows**: the Ryuk reaper sidecar flakes with
  "Port mapping ... port 8080 is not available", causing intermittent skips. Fix applied in
  `tests/conftest.py`: `TESTCONTAINERS_RYUK_DISABLED=true` + each fixture stops its own container
  in `finally`. Carry this env var into the F04 CI config too.
- Docker Desktop daemon must be running before DB-backed tests; the daemon needs ~30–60s after
  launch before it serves (early calls fail fast → tests skip). Poll `docker info` before running.
- **`SET LOCAL app.org_id = :org` is INVALID** — Postgres `SET`/`SET LOCAL` takes a literal token
  and rejects bind parameters, so the parameterised statement fails to parse. `tenant_session` MUST
  use `SELECT set_config('app.org_id', :org, true)` (the third arg `is_local => true` makes it
  transaction-scoped, the function equivalent of `SET LOCAL`, and accepts a bound value safely).
  Found in F02 (2026-06-22). Context docs (`architecture.md` + `librarydocs.md`) corrected — do NOT
  revert the snippet back to `SET LOCAL`.
