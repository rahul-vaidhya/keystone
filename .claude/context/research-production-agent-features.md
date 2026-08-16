# Research: production-grade retrieval, observability, evals & feedback (2026-07-27)

> Six-agent research pass (WebSearch-backed, 2025-2026 sources), commissioned to answer:
> "what would make Veratas properly production-ready and better than the competition" —
> covering retrieval quality/reranking, vector-store infra, LLM observability/tracing,
> evals + feedback loops, tool-calling + model config, and a direct feature comparison
> against Databricks Agent Bricks / Google ADK / Microsoft Copilot Studio. This is a
> **reference document for a future `/architect` session** — nothing here is built yet.
> Read this before scoping any of: the reranker seam, hybrid search, the eval harness,
> the feedback table, or observability work. Full per-agent reports (with sources) are
> preserved in the session transcript this doc was written from; this is the synthesis.

## The bug that triggered this research

Retrieval (`RetrievalService.search`, F31) has **no distance/confidence threshold** — it
always returns exactly the top-k nearest chunks by raw cosine distance, regardless of how
weak the match is. When the true answer chunk isn't a strong nearest-neighbor (phrasing
mismatch, fact split across a chunk boundary), the LLM receives irrelevant context and
correctly refuses per its grounding contract — the refusal is *working as designed*, but
retrieval failed to surface the right evidence. This is a retrieval-recall problem, not a
prompt problem — loosening the refusal contract would reintroduce the hallucination risk
the project deliberately closed off (F40's manual acceptance gate). All research below
converges on: **reranker + hybrid search fix this**, not prompt changes.

---

## Consolidated cross-cluster priority list

**P0 — ship next, highest confidence of directly fixing the reported bug:**
1. **`Reranker` seam** (`app/services/seams/reranker.py`) — new Protocol mirroring
   `Parser`/`Embedder`/`LLM` exactly: `rerank(query, candidates: list[ChunkHit], top_k) ->
   list[ChunkHit]`. `RERANKER_MODE=fake|real` (fake = identity/pass-through, for tests).
   Real impl: self-hosted **BGE-reranker-v2-m3** (Apache-2.0, ~50-100ms, near-zero
   marginal cost) — NOT LLM-as-reranker (9x cost, 35x latency, modest gain). `RetrievalService.search`
   widens the candidate kNN to `RERANK_CANDIDATE_K` (~25), reranks, keeps `RERANK_TOP_K`
   (~6-8). This is the item already named "reranker seam" in `buildplan.md`'s Postponed
   V2 list — this research operationalizes it.

   > **Addendum (2026-08-04, locked decision):** self-hosted TEI is confirmed the right call for
   > local/offline dev (no per-call cost, no external data exposure) but is too CPU-heavy to run
   > as production infra for this team (~11.5 min warmup confirmed 2026-07-30). Production will
   > use a **hosted cross-encoder-as-a-service API** (Cohere Rerank / Jina Reranker / Voyage
   > rerank-2) instead — still a genuine cross-encoder, not an LLM prompt, so the LLM-as-reranker
   > rejection above still holds. This is a pure `Reranker`-Protocol adapter swap (new
   > `real_reranker_<vendor>.py`), no change to `RetrievalService`. Full detail:
   > `architecture.md`'s "Reranker" section and the Build-now/Postponed table.
2. **Confidence gate using the reranker score, not raw cosine distance.** Raw distance
   cutoffs don't generalize across corpora/models. If the top reranked score falls below
   `RERANK_MIN_SCORE` (tune empirically, same methodology as the 2026-07-16 eval
   harness), short-circuit before the LLM call — return a distinct "weak/no evidence"
   response instead of paying for a call that will just refuse.
3. **Hybrid search (BM25 lexical + vector, fused via RRF).** Second-highest-confidence
   fix — Veratas' real corpus (business PDFs: product names, IDs, acronyms, exact terms)
   is exactly the failure mode dense-only embeddings miss. Postgres-native, no new
   infra: `pg_search` (ParadeDB) or `pg_textsearch` (Tiger Data) extension, new
   `tsvector`+GIN migration on `chunks.content`, a new `ChunkRepository.search_chunks_lexical`
   sibling to the existing `search_chunks`, fused by a new pure function `fuse_rrf()` in
   `services/retrieval/` (no SQL, same shape as the existing `assemble_context`). Flag
   `HYBRID_SEARCH_ENABLED`. This is also already named in buildplan.md's Postponed V3 list.
4. **`message_feedback` table + wire up the dead thumbs buttons.** The existing
   thumbs up/down UI control does nothing today (UI-only, unpersisted, per
   `progresstracker.md`). Schema: `id, org_id, message_id (FK messages), user_id,
   rating (up|down), reason_tags (text[]), comment (text, nullable), corrected_answer
   (text, nullable), created_at`. New `POST /chat/messages/{id}/feedback`. This is the
   cheapest, most concrete step toward a real feedback loop and toward beating Copilot
   Studio's 28-day comment-retention gap outright.
5. **A durable, SME-reviewable golden-question eval fixture.** Formalize the ad hoc
   pattern already prototyped in `test_hierarchical_eval.py` into a permanent regression
   suite (`pytest -m eval`, CI-excluded like `real_parser`/`hierarchical_eval`) using
   **Ragas** metrics (faithfulness, answer relevancy, context precision/recall) against a
   golden set that's admin-curatable from real `message_traces` rows (see P1 below), not
   a hand-written Python list. This is Veratas' actual answer to Agent Bricks' automatic
   evaluation and to ADK's `EvalSet` pattern.

**P1 — highest additional leverage, most of it reuses infrastructure that already exists but is dormant:**
6. **Contextual retrieval** (Anthropic's technique: prepend a short LLM-generated
   context blurb to each chunk before embedding — NOT before showing it to the LLM as
   context, only for the embedding call). Published results: ~49% fewer retrieval
   failures alone, ~67% combined with reranking. **The standout finding of this whole
   research pass**: Veratas already computes per-section LLM summaries
   (`ENRICHMENT_ENABLED`, `sections.summary`/`topics`) and they're dormant — this is
   "use infrastructure you already built" more than "build something new." Extend
   `app/services/ingestion/enrichment.py`; new flag `CONTEXTUAL_EMBEDDING_ENABLED`;
   requires a backfill re-embed of existing READY docs (same shape as the existing
   `enrich-backfill` admin endpoint). Re-evaluate `HIERARCHICAL_RETRIEVAL_ENABLED`
   *after* this ships — the 2026-07-16 null result may be an artifact of
   context-free chunk embeddings, not proof hierarchy itself doesn't help.
7. **Self-hosted MLflow (tracing + `mlflow.genai.evaluate()` + Labeling Sessions).**
   This is the single most important finding from the evals/feedback report: MLflow OSS
   already ships the Review-App/labeling primitive that Databricks Agent Bricks' labeling
   UI is *built on top of*. Don't hand-roll a labeling UI from scratch — instrument
   `ChatService.ask`/`stream_ask` to emit an MLflow trace per call (reusing the
   `message_traces` payload — hits, prompt, answer, citations), then a small admin
   `EvalDatasetPage.tsx` that lists recent traces with an "add to golden set" action.
   Also gives Ragas-as-MLflow-judge integration for free.
8. **OpenTelemetry GenAI semantic conventions + self-hosted Langfuse or Arize Phoenix.**
   Adopt `gen_ai.*` attribute names in new spans immediately (free, vendor-neutral) even
   before wiring an exporter. **Not a 4th seam** — tracing is cross-cutting infra, not
   swappable business logic; there's no meaningful "fake tracer" the way there's a
   `FakeEmbedder`. New `app/config/telemetry.py` (OTel SDK init, OTLP exporter, env-gated
   off in tests — zero network calls in CI). Instrument retrieval sub-spans, the LLM call,
   and each ingestion stage. **Self-hosted only, by default** — prompts contain
   customer-document content, so a SaaS-only tracing platform breaches the same trust
   boundary the product is selling protection from. Keep `message_traces`/the admin Debug
   toggle as-is; it's cheap and already works as the end-user-facing view.
9. **Structured-extraction mini-brick.** The most literal capability gap vs. Databricks
   Agent Bricks' "Information Extraction": natural-language field description → LLM
   proposes a JSON schema → per-document extraction → user corrects a few examples →
   re-run. Bounded, not a research project — directly extends the existing per-section
   LLM-call pattern in `enrichment.py`. No lakehouse/Delta/Unity-Catalog equivalent
   needed, just a schema-guided extraction endpoint.
10. **Hardcode grounded-QA generation params.** `GROUNDED_QA_TEMPERATURE=0.1`,
    `GROUNDED_QA_TOP_P=0.9` in `app/config/settings.py`, threaded through
    `RealLLM`/`FakeLLM` call sites — today these are unset (provider default), an
    unintentional gap. Do NOT expose per-org/per-request overrides — no evidence of
    demand, real support-surface risk (customers tuning themselves into worse
    hallucination behavior).
11. **Extend the `LLM` seam Protocol with an optional `tools`/`ToolCall` shape**, purely
    preparatory — additive, byte-identical behavior for existing callers that never pass
    `tools`. Do not build an agent loop yet (see P2).

**P2 — real, but gated behind a trigger condition; do not build speculatively:**
- **Query rewriting/multi-query retrieval** — only after P0 ships and a follow-up eval
  (same real-seam methodology as the 2026-07-16 harness) shows a residual
  phrasing-mismatch failure class. Do **not** build HyDE — 2025 research found it
  "consistently hurt performance" by distorting intent; skip entirely.
- **Agentic multi-hop retrieval** (LLM calls `search_documents` as a tool, possibly
  multiple times) — only once a genuinely large multi-document notebook produces
  *observed* comparative/synthesis-question failures in production, not hypothetically.
  Ship as an explicit opt-in mode, never a silent upgrade to `/chat/ask` — most queries
  need the deterministic single-hop latency contract.
- **`WebSearch` seam** — off by default if ever built, mirroring the existing seam
  pattern exactly (`WEB_SEARCH_MODE`, fake+real). **Hard requirement if built**: any
  web-sourced answer must be visually/structurally distinct from document-grounded
  answers (e.g. a "🌐 Web" badge, never merged into the `[n]` citation list) — blending
  them would quietly destroy Veratas' core "only knows what you gave it" trust
  guarantee. NotebookLM's constrained posture is the right reference product here, not
  Perplexity's search-native model.
- **Model-aware `LLMConfig` / per-task model routing** (cheap model for query rewriting,
  strong model for final answer) — worth doing once a second real LLM-driven task type
  exists; premature today. Keep it as ONE `LLM` Protocol with named model configs
  resolved through the existing `get_llm()` factory, not a second seam type.
- **DSPy-style automated prompt optimization** (MIPRO/BootstrapFewShot) against the
  labeled/golden set — explicitly NOT autonomous auto-deploy. Candidate prompts must
  still pass the eval gate before an engineer promotes them by hand. This is the honest,
  right-sized version of what Databricks' TAO/DSPy loop does; full unattended
  optimization is enterprise-ML-infra scale, out of reach for a small team and not worth
  chasing.
- **pgvectorscale / `halfvec` adoption** — trigger: `embeddings` table exceeds
  ~10-20M rows AND measured HNSW recall/latency degrades under real load. Both are
  additive Postgres extensions (no new database, no RLS-equivalent gap) — prefer these
  over migrating off pgvector entirely.
- **Migration off pgvector** (Weaviate/Qdrant native multi-tenancy) — only if
  pgvectorscale's ceiling is *also* exceeded, or hybrid/native reranking becomes a hard
  requirement Postgres extensions can't match. Explicitly NOT warranted today, and would
  require re-deriving an RLS-equivalent tenant-isolation guarantee outside Postgres — a
  real regression risk against the F60 hard rule ("RLS is the non-negotiable backstop").
- **Structured/tabular source fusion** (Agent Bricks' Knowledge Assistant's biggest edge
  over Veratas) — only worth building once a real customer needs numbers-plus-documents
  in one notebook. Sketch: a CSV/Excel-to-row-store adapter feeding `chunks`/`embeddings`
  via a new `owner_type='row'`, reusing existing retrieval — no lakehouse needed.

---

## Where Veratas already matches or beats the competition (don't touch)

- **Citation/grounding trust UX**: the fixed refusal string + never-blend-in-training-
  knowledge contract is *stricter* than Copilot Studio's "allow ungrounded responses"
  toggle and already matches Databricks' citation-required posture. State this
  explicitly in positioning — it's a real, already-shipped differentiator.
- **Permission-aware retrieval**: Access Roles (tag-based RBAC) + notebook privacy
  (2026-07-27) already do what Glean cites as its edge (permission-inheriting retrieval)
  — no gap here versus the field.
- **`WHERE model = :active_model` embedding-version filtering**: confirmed by the
  vector-infra research as exactly the standard "alias swap" pattern for live re-embed
  without downtime — no change needed.

## Feature-gap snapshot vs. Databricks Agent Bricks / Google ADK / Copilot Studio

| Capability | Veratas | Databricks | ADK | Copilot Studio |
|---|---|---|---|---|
| Structured+unstructured hybrid retrieval | Missing (P2 above) | Has | N/A | Missing |
| Iterative schema-guided extraction | Missing (P1 above) | Has | N/A | Missing |
| Auto prompt/quality optimization from feedback | Missing (P2, scoped down) | Has (DSPy+TAO+MLflow) | Eval framework only | Missing |
| SME-facing labeling UI | Dormant data, no UI (P1 above) | Has (Review App) | Missing | Partial, 28-day retention |
| Built-in tracing/evals | Partial (`message_traces`, no eval suite) (P0/P1 above) | Has (MLflow 3) | Has (`EvalSet`) | Partial |
| Reranking | Missing (P0 above) | Implied in "70% quality" claim | N/A | Unclear |
| Citation/grounding trust UX | **Strong, already shipped** | Has | N/A | Has, less strict |

## Notes on methodology / honesty of the sources

- The retrieval-quality and vector-infra reports both independently converged on hybrid
  search + reranking as higher-leverage than the previously-built V2 hierarchical
  retrieval — consistent with, not contradicting, the 2026-07-16 eval harness's finding
  that hierarchical retrieval measured zero improvement on the single-document test
  corpus. The interpretation across all reports: **retrieval precision, not retrieval
  architecture depth, is the actual lever** for Veratas' corpus shape.
- Every report was explicitly instructed to map recommendations onto Veratas' existing
  module/seam boundaries rather than propose a generic rewrite — this is why the reranker
  and web-search proposals above are shaped as new seams (Protocol + fake + real,
  config-selected) rather than ad hoc integrations.
- Full source lists (30+ links across all six reports — vendor docs, benchmarks, and
  2025-2026 engineering blog posts) exist in the original agent outputs; ask to have them
  re-surfaced for any specific item above before committing to an implementation.
