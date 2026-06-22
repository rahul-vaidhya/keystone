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

    # --- Postgres (async driver) ---
    DATABASE_URL: str = "postgresql+asyncpg://veratas:veratas@localhost:5432/veratas"

    # --- Redis (arq queue) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Tenancy ---
    # Enforced RLS is DESIGNED now and switched on in Phase 6. App-level org_id scoping
    # is ALWAYS on regardless of this flag. Keep OFF in dev/test.
    RLS_ENABLED: bool = False

    # --- Seams ---
    # fake | real. Real adapters (vendor TBD) land in F03. The whole app + test suite
    # runs on fakes with no API keys.
    SEAMS_MODE: str = "fake"

    # --- Cloudflare R2 (S3-compatible object store) ---
    R2_ENDPOINT_URL: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None


settings = Settings()
