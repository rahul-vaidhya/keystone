"""Background job queue (arq-backed). Not one of the 3 seams (architecture.md:
Parser/Embedder/LLM only) and not a 4th seam — same treatment as ``platform/storage.py``'s
``ObjectStore``: a thin Protocol + DI'd dependency, so tests override it with an in-memory
fake instead of needing a real Redis/arq worker.

F24: ``documents`` module's upload endpoint enqueues the first ingestion stage's job here;
each stage's job (``ingestion/tasks.py``) enqueues the next stage's job on success — never
the whole chain upfront.

``job_id`` is the REAL correctness guarantee against duplicate enqueues — not just an
optimization. arq refuses to create a second job sharing an already-queued/active
``_job_id`` (review finding, fixed here): a before/after DB-status comparison alone
cannot detect a concurrent redelivery, because the "before" read happens in a separate
transaction from the stage's actual claim — two concurrent deliveries can both read the
same "before" status and both decide to enqueue. ``ingestion/tasks.py`` passes a
deterministic ``job_id`` (``f"ingestion:{stage}:{document_id}"``) on every next-stage
enqueue specifically so arq itself — not application logic — is what prevents the
duplicate, regardless of timing.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from app.config.settings import settings


class JobQueue(Protocol):
    async def enqueue(
        self, function: str, *, job_id: str | None = None, **kwargs: object
    ) -> None: ...


_shared_pool: object | None = None
_shared_pool_lock = asyncio.Lock()


async def _get_shared_pool() -> object:
    """One process-wide pool for the HTTP path (mirrors ``platform/db.py``'s module-level
    engine/sessionmaker pattern) — without this, every request that enqueues a job would
    open its own Redis connection and never close it."""
    global _shared_pool
    if _shared_pool is None:
        async with _shared_pool_lock:
            if _shared_pool is None:
                from arq.connections import RedisSettings, create_pool

                _shared_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _shared_pool


class ArqJobQueue:
    """Real adapter. With no ``pool`` given, lazily resolves the shared process-wide pool
    (HTTP path). The worker passes its OWN pool explicitly (``ctx["redis"]``, see
    ``worker.py``'s ``startup`` hook) so it never touches the shared one."""

    def __init__(self, pool: object | None = None) -> None:
        self._pool = pool

    async def _get_pool(self) -> object:
        if self._pool is None:
            self._pool = await _get_shared_pool()
        return self._pool

    async def enqueue(self, function: str, *, job_id: str | None = None, **kwargs: object) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(function, _job_id=job_id, **kwargs)  # type: ignore[attr-defined]


def get_job_queue() -> JobQueue:
    """FastAPI dependency factory. Tests override this via ``app.dependency_overrides``
    with an in-memory fake instead of constructing a real arq/Redis pool."""
    return ArqJobQueue()
