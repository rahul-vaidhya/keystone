"""Fail-fast database check run when the API starts (``main.py`` lifespan).

Without it, a fresh setup with Postgres down or migrations not applied still starts,
and every request (even sign-up / sign-in) then fails with a bare 500 Internal Server
Error whose cause is only visible in the server log. This turns both cases into a
startup failure that says exactly what to run.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.config.db import engine
from app.config.settings import settings

_BACKEND = Path(__file__).resolve().parents[2]


class StartupCheckFailed(RuntimeError):
    """The database is unreachable or its schema is not at the latest migration."""


def _head_revision() -> str | None:
    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def check_database() -> None:
    head = _head_revision()
    try:
        async with engine.connect() as conn:
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except ProgrammingError:  # alembic_version table missing: never migrated
        current = None
    except Exception as exc:  # noqa: BLE001 -- any connect failure gets the same advice
        raise StartupCheckFailed(
            f"Cannot connect to Postgres at {engine.url.render_as_string(hide_password=True)} "
            f"({type(exc).__name__}: {exc}). Start Docker Desktop, then run "
            "`docker compose up -d postgres redis` from the repo root, and check "
            "DATABASE_URL in backend/.env."
        ) from exc
    if current != head:
        raise StartupCheckFailed(
            f"Database schema is at {current or 'nothing (no tables)'}, expected {head}. "
            "Run `alembic upgrade head` from backend/ (venv active), then start the API again."
        )


def enabled() -> bool:
    return settings.STARTUP_DB_CHECK
