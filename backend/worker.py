"""arq entrypoint. Run with `arq worker.WorkerSettings`.

F24 registers the ingestion pipeline's 3 stage jobs. Each opens DB work through the
shared `tenant_session(org_id)`-backed services (F02) using the `org_id` carried on the
job payload, exactly as the HTTP path does.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.config.logging import configure_logging
from app.config.settings import settings
from app.services.ingestion.tasks import (
    run_embedding_stage_job,
    run_enrichment_stage_job,
    run_parsing_stage_job,
    run_structuring_stage_job,
)
from app.services.queue import ArqJobQueue


async def startup(ctx: dict) -> None:
    """arq lifecycle hook — configure structlog so worker jobs emit the same JSON logs as
    the HTTP edge (which calls configure_logging() in main.py), and bind a JobQueue that
    reuses arq's own redis pool (``ctx["redis"]``) so stage-chaining jobs don't open a
    second connection."""
    configure_logging()
    ctx["job_queue"] = ArqJobQueue(pool=ctx["redis"])


class WorkerSettings:
    functions: list = [
        run_parsing_stage_job,
        run_structuring_stage_job,
        run_embedding_stage_job,
        run_enrichment_stage_job,
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    on_startup = startup
