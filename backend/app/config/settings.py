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
    DATABASE_URL: str = "postgresql+asyncpg://veratas:veratas@localhost:5432/veratas"

    # --- Redis (arq queue) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Tenancy ---
    # Enforced RLS is DESIGNED now and switched on in Phase 6. App-level org_id scoping
    # is ALWAYS on regardless of this flag. Keep OFF in dev/test.
    RLS_ENABLED: bool = False

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


settings = Settings()
