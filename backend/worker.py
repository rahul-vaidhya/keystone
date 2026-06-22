"""arq entrypoint. Run with `arq worker.WorkerSettings`.

Phase 0 registers no tasks. Ingestion tasks (Phase 2) will be added to `functions`, and
each will open DB work through the shared `tenant_session(org_id)` helper (F02) using the
`org_id` carried on the job payload.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.platform.config import settings
from app.platform.logging import configure_logging


async def startup(ctx: dict) -> None:
    """arq lifecycle hook — configure structlog so worker jobs emit the same JSON logs as
    the HTTP edge (which calls configure_logging() in main.py)."""
    configure_logging()


class WorkerSettings:
    functions: list = []
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    on_startup = startup
