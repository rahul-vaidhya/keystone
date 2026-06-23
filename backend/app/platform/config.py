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
    # fake | real. Fakes are the default everywhere (no API keys, deterministic). Real
    # adapters land per seam: Embedder/LLM here (F03), Parser/OCR vendor in Phase 2 (F20).
    SEAMS_MODE: str = "fake"
    # Real Embedder/LLM target an OpenAI-compatible API (used only when SEAMS_MODE=real);
    # the models live behind the seam, so they stay swappable. text-embedding-3-small is
    # 1536-d → matches the vector(1536) column; LLM_MODEL is a mini-class default.
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_MODEL: str = "gpt-4o-mini"

    # --- Cloudflare R2 (S3-compatible object store) ---
    R2_ENDPOINT_URL: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None


settings = Settings()
