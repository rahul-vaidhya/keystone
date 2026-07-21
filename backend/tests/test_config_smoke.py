"""F00 smoke: configuration loads with the right defaults and honors env overrides."""

from __future__ import annotations

from app.config.settings import Settings, settings


def test_defaults() -> None:
    # RLS_ENABLED defaults True post-F60 (enforced RLS shipped); the field itself is now
    # vestigial, kept only because migrations/0002 imports it at runtime.
    assert settings.RLS_ENABLED is True
    # no vendor keys needed in dev/test — each seam defaults to fake independently (F23)
    assert settings.PARSER_MODE == "fake"
    assert settings.EMBEDDER_MODE == "fake"
    assert settings.LLM_MODE == "fake"
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")  # async driver


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("RLS_ENABLED", "true")
    assert Settings().RLS_ENABLED is True
