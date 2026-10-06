"""Application configuration via pydantic-settings.

Single source of truth for environment-driven config. Read it through the module-level
`settings` singleton; never read `os.environ` directly elsewhere.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    # Comma-separated origins for the Vite dev server / SPA.
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Auth (F10) ---
    JWT_SECRET: str = "dev-only-change-me-use-32-chars-min!!"
    JWT_ACCESS_TTL_MINUTES: int = 15
    JWT_REFRESH_TTL_DAYS: int = 7
    REFRESH_COOKIE_NAME: str = "veratas_refresh"

    # --- Postgres (async driver) ---
    # DATABASE_URL is what the RUNNING APP (API + arq worker) connects with — in an
    # RLS-enforced deployment that's the restricted `app_user` role. Migrations need the
    # privileged table-owning role (`migrator`/owner), so Alembic prefers
    # MIGRATIONS_DATABASE_URL when set and falls back to DATABASE_URL (dev/test use one
    # superuser URL for both; superusers bypass RLS even under FORCE).
    DATABASE_URL: str = "postgresql+asyncpg://veratas:veratas@localhost:5432/veratas"
    MIGRATIONS_DATABASE_URL: str | None = None

    # --- Redis (arq queue) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Tenancy ---
    # F60 (Phase 6) made RLS enforcement UNCONDITIONAL: migration 0015 applies policies +
    # FORCE RLS regardless of any flag, and tenant_session always sets the app.org_id
    # GUC. This flag is now vestigial — it survives only because migration 0002's
    # (historically flag-gated, superseded-by-0015) body imports it at runtime; deleting
    # the field would crash every fresh-DB migration run. Do not gate new code on it.
    RLS_ENABLED: bool = True

    # --- Seams ---
    # fake | real, ONE SWITCH PER SEAM (decided F23 — was a single SEAMS_MODE before).
    # Fakes are the default everywhere (no API keys, deterministic); each seam opts into
    # real independently, e.g. PARSER_MODE=real with EMBEDDER_MODE/LLM_MODE left on fake.
    PARSER_MODE: str = "fake"
    EMBEDDER_MODE: str = "fake"
    LLM_MODE: str = "fake"
    # Real Embedder/LLM target an OpenAI-compatible API (used only when EMBEDDER_MODE/
    # LLM_MODE=real); the models live behind the seam, so they stay swappable.
    # text-embedding-3-small is 1536-d → matches the vector(1536) column; LLM_MODEL is a
    # mini-class default.
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_MODEL: str = "gpt-4o-mini"
    # Real Parser (F23) targets OpenRouter's file-parser plugin — a SEPARATE adapter/vendor
    # from the OpenAI-compatible Embedder/LLM above, even though both ultimately go through
    # an OpenRouter-compatible endpoint. PARSER_MODEL is incidental: the chat/completions
    # call's generated text is discarded — only the plugin's file annotations are read — so
    # any cheap model slug works. PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE is the routing
    # threshold: try the free `cloudflare-ai` text engine first, fall back to billed
    # `mistral-ocr` only if the text engine returns fewer than this many chars per page
    # (i.e. the PDF is scanned/image-only).
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    PARSER_MODEL: str = "openai/gpt-4o-mini"
    PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE: int = 20

    # --- Semantic structuring / V2 (semantic outline post-pass) ---
    SEMANTIC_OUTLINE_ENABLED: bool = False
    SEMANTIC_OUTLINE_WINDOW_CHARS: int = 24000

    # --- Enrichment / V2 (section summaries + section embeddings) ---
    ENRICHMENT_ENABLED: bool = False
    ENRICHMENT_SECTION_CHAR_LIMIT: int = 6000

    # --- Hierarchical retrieval / V2 (coarse-to-fine) ---
    HIERARCHICAL_RETRIEVAL_ENABLED: bool = False
    HIERARCHICAL_TOP_SECTIONS: int = 8

    # --- Reranker / V2 (architecture.md "A Reranker seam is added in V2, not now") ---
    # RERANKER_ENABLED is a SEPARATE switch from RERANKER_MODE: it gates whether
    # reranking logic runs AT ALL (same off-by-default pattern as
    # HIERARCHICAL_RETRIEVAL_ENABLED/ENRICHMENT_ENABLED — shipping this changes nothing
    # in existing behavior until explicitly turned on). RERANKER_MODE only matters once
    # RERANKER_ENABLED=true — it picks fake vs real, exactly like PARSER_MODE/
    # EMBEDDER_MODE/LLM_MODE.
    RERANKER_ENABLED: bool = False
    RERANKER_MODE: str = "fake"
    # RealReranker is an HTTP client for a self-hosted BGE-reranker-v2-m3 instance served
    # by Hugging Face Text-Embeddings-Inference (see docker-compose.yml's `reranker`
    # service) — not a vendor SDK, so there is no API key setting here.
    RERANKER_URL: str | None = None
    # candidate_k = max(req.k, RERANK_CANDIDATE_K) widens the chunk kNN pool BEFORE
    # reranking; final_k = min(req.k, RERANK_TOP_K) is the truncated, reordered result
    # size AFTER reranking. Mirrors the existing hierarchical-retrieval widening pattern
    # (`s = max(k, HIERARCHICAL_TOP_SECTIONS)` in services/retrieval.py).
    RERANK_CANDIDATE_K: int = 25
    RERANK_TOP_K: int = 8
    # Confidence gate: if the TOP result's rerank_score falls below this threshold, chat
    # skips the LLM call entirely and returns a distinct "weak evidence" response instead
    # (see services/chat/service.py). No separate enable flag — rerank_score is always
    # None when RERANKER_ENABLED=False, so the gate structurally cannot fire until
    # reranking is on. Deliberately permissive default: turning RERANKER_ENABLED=True on
    # does NOT immediately start gating answers — raise this once real score
    # distributions from production traffic are known.
    RERANK_MIN_SCORE: float = -10.0

    # --- Hybrid search / V2+ (native Postgres full-text + vector, fused via RRF) ---
    # Off by default (same pattern as RERANKER_ENABLED/HIERARCHICAL_RETRIEVAL_ENABLED) —
    # shipping this changes nothing in existing retrieval behavior until explicitly
    # turned on. Lexical backend is native Postgres full-text search (tsvector/GIN,
    # migration 0021) — no third-party extension, no Docker image change.
    HYBRID_SEARCH_ENABLED: bool = False
    # Both the vector and lexical candidate pools are widened to
    # max(k, HYBRID_CANDIDATE_K) before RRF fusion — mirrors RERANK_CANDIDATE_K's
    # widening pattern. When RERANKER_ENABLED is also on, the effective candidate pool
    # is max(k, RERANK_CANDIDATE_K, HYBRID_CANDIDATE_K) (see services/retrieval.py).
    HYBRID_CANDIDATE_K: int = 25
    # From-scratch sparse IR lexical channel (app/services/retrieval/sparse/ +
    # sparse_channel.py). "off" (default) keeps the Postgres tsvector/ts_rank lexical
    # channel byte-identical. "tfidf" (SMART lnc.ltc) / "bm25" REPLACE that lexical
    # channel with an in-house, inspectable inverted index — ONLY when
    # HYBRID_SEARCH_ENABLED is also on (it is never a standalone retriever). Fusion,
    # reranking and the confidence gate are unchanged.
    SPARSE_RETRIEVAL_MODE: Literal["off", "tfidf", "bm25"] = "off"
    # Zone weight of the chunk's section heading (the body zone is always 1.0).
    SPARSE_HEADING_ZONE_WEIGHT: float = 2.0

    # --- Per-sentence citation checker (app/services/chat/citation_check.py) ---
    # Off by default: when on, every normal (chunk-cited) answer is split into sentences
    # and each sentence is scored against the chunks it cites — lexical = tf-idf cosine
    # over the from-scratch sparse index, semantic = embedding cosine (one batched
    # Embedder call) — and the result is returned + persisted as messages.claim_checks.
    CITATION_CHECK_ENABLED: bool = False
    # Threshold on the COMBINED score (mean of lexical + semantic, where lexical = max
    # tf-idf cosine over the cited chunk's sentence windows and semantic = embedding
    # cosine against the whole chunk). Calibrated 2026-10-07 on kech104.pdf with
    # text-embedding-3-small: 35 cited/inherited answer sentences vs. the same sentences
    # paired with a random OTHER retrieved chunk (same-domain negatives) -> supported
    # p10 0.37 / median 0.57, negatives median 0.23 / p90 0.28; 0.33 separates 35/35
    # positives from 34/35 negatives. Re-calibrate if the embedder model changes.
    CITATION_SUPPORT_THRESHOLD: float = 0.33

    # --- Broad-query map-reduce / P1 (memory.md "P1 roadmap") ---
    # Off by default (same pattern as HIERARCHICAL_RETRIEVAL_ENABLED/RERANKER_ENABLED/
    # HYBRID_SEARCH_ENABLED) — shipping this changes nothing in existing chat behavior
    # until explicitly turned on. When enabled, a cheap LLM classifier call
    # (services/chat/broad_query.py) routes "broad"/aggregate questions (e.g. "what's
    # the gist of this notebook") to a map-reduce pass over V2 enrichment section
    # summaries (services/retrieval/mapreduce.py) instead of top-k chunk search;
    # "specific" questions, and any notebook lacking enrichment or exceeding
    # BROAD_QUERY_MAX_DOCUMENTS, fall through to the existing flat/hybrid/rerank
    # pipeline unchanged.
    BROAD_QUERY_ENABLED: bool = False
    BROAD_QUERY_MAX_DOCUMENTS: int = 20

    # --- Notebook Overview / P1 (memory.md "P1 roadmap") ---
    # Off by default (same pattern as BROAD_QUERY_ENABLED). Gates ONLY generation
    # (``KnowledgeService.generate_overview``) — reading an already-cached overview via
    # GET stays available even if this is later turned off, mirroring how turning off
    # RERANKER_ENABLED/HYBRID_SEARCH_ENABLED doesn't retroactively erase already-computed
    # rerank_score/content_tsv values. Reuses BROAD_QUERY_MAX_DOCUMENTS as its
    # document-count safety cap and the same map-reduce mechanism
    # (services/retrieval/mapreduce.py) — no separate cap setting needed.
    NOTEBOOK_OVERVIEW_ENABLED: bool = False

    # --- Contextual embedding / P1 (memory.md "P1 roadmap") ---
    # Off by default (same pattern as every other V2/P1 flag) — shipping this changes
    # nothing until explicitly turned on. When enabled, `run_enrichment_stage` (V2
    # enrichment) re-embeds a section's chunks IN PLACE (same `owner_type='chunk'` rows,
    # same `unique(owner_type, owner_id, model)` upsert the original F22 embedding stage
    # uses) with the section's own summary prepended to each chunk's raw text — Anthropic's
    # "contextual retrieval" technique, but reusing the EXISTING V2 enrichment summary as
    # the context blurb instead of a new per-chunk LLM call (zero new LLM calls beyond what
    # enrichment already makes). Only takes effect once enrichment has run for a document;
    # a document with no enrichment yet simply keeps its original context-free chunk
    # embeddings, same fallback shape as every other V2 capability in this codebase.
    CONTEXTUAL_EMBEDDING_ENABLED: bool = False

    # --- Chat (F40) ---
    # The LLM seam call gets an explicit timeout + retry-with-backoff, but ONLY on
    # transient failures (SeamTransientError from the seam, or our own timeout) — never on
    # a bug, never as a broad except-Exception inside the retry loop.
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_BASE_SECONDS: float = 0.5

    # --- Object store (F05) ---
    # r2 | local. Default r2 (production), mirroring the seam *_MODE fake-default pattern:
    # local is opt-in, selects LocalDiskObjectStore so the full product (API + arq worker)
    # runs with zero cloud creds. STORAGE_LOCAL_ROOT is only used when STORAGE_MODE=local.
    STORAGE_MODE: str = "r2"
    STORAGE_LOCAL_ROOT: str = "./.localstorage"

    # --- Cloudflare R2 (S3-compatible object store; used when STORAGE_MODE=r2) ---
    R2_ENDPOINT_URL: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None

    # --- Embed widget (docs/embed-widget-plan.md) ---
    # Base URL of the deployed frontend SPA — used to build the admin-facing embed
    # snippet/iframe URL (WidgetOut.embed_snippet/iframe_url). Not the backend's own
    # URL; the widget.js/iframe are served by the SPA, not this API.
    PUBLIC_APP_URL: str = "http://localhost:5173"
    # Redis sliding-window rate limits on the PUBLIC (unauthenticated) embed chat
    # endpoint — per-widget and per-IP, both enforced (see app/utils/rate_limit.py).
    WIDGET_RATE_LIMIT_PER_MINUTE: int = 30
    WIDGET_IP_RATE_LIMIT_PER_MINUTE: int = 10
    # Comma-separated IPs of reverse proxies/load balancers this app runs behind, in
    # front of the PUBLIC embed endpoint. Empty (default) = trust nothing, use the
    # raw socket peer address (correct for local dev / direct connections). When a
    # request's peer address IS one of these, app/utils/http.py:get_client_ip reads
    # the real visitor IP from X-Forwarded-For instead — otherwise per-IP rate
    # limiting silently degrades to a single shared bucket for every visitor behind
    # the proxy. Never trust X-Forwarded-For from a peer NOT in this list (any client
    # can forge that header directly).
    TRUSTED_PROXY_IPS: str = ""


settings = Settings()
