"""Application configuration via pydantic-settings.

Single source of truth for environment-driven config. Read it through the module-level
`settings` singleton; never read `os.environ` directly elsewhere.
"""

from __future__ import annotations

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
