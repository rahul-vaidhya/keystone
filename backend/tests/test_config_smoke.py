"""F00 smoke: configuration loads with the right defaults and honors env overrides."""

from __future__ import annotations

from app.platform.config import Settings, settings


def test_defaults() -> None:
    assert settings.RLS_ENABLED is False  # enforced RLS is OFF until Phase 6
    # no vendor keys needed in dev/test — each seam defaults to fake independently (F23)
    assert settings.PARSER_MODE == "fake"
    assert settings.EMBEDDER_MODE == "fake"
    assert settings.LLM_MODE == "fake"
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")  # async driver


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("RLS_ENABLED", "true")
    assert Settings().RLS_ENABLED is True
