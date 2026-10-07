"""Test fixtures.

The DB-backed fixtures spin an ephemeral Postgres+pgvector via Testcontainers and apply
the REAL Alembic migration (so the F01 smoke test exercises the actual schema, never a
mocked DB — per codestandards). They skip cleanly when Docker is unavailable, so the
pure smoke tests still run anywhere. The full Testcontainers CI harness is wired in F04.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Disable the Testcontainers Ryuk reaper: its sidecar port mapping flakes on Docker
# Desktop / Windows. Each fixture stops its own container in a finally block instead.
# Read by testcontainers at its (in-fixture) import, so setting it here is in time.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
# Tests assume the fake/off defaults: never read backend/.env (app/config/settings.py).
os.environ["KEYSTONE_IGNORE_DOTENV"] = "1"
# Tests build their own Testcontainers DB per fixture; the API startup DB check is for
# real runs only (app/config/startup_check.py).
os.environ["STARTUP_DB_CHECK"] = "false"

BACKEND = Path(__file__).resolve().parents[1]


class FakeJobQueue:
    """F24 test double for ``platform.queue.JobQueue`` — records every enqueue call
    instead of touching real Redis/arq. Shared across test files that hit
    ``/documents/upload`` (which now enqueues the parsing job on every genuine new
    upload).

    Models arq's real ``_job_id`` dedup: a second enqueue sharing an already-seen
    ``job_id`` is silently dropped (not recorded), same as real arq refusing to create a
    second job with an ID already queued/active. This is the mechanism that actually
    prevents a double-enqueue under concurrent redelivery (see ``ingestion/tasks.py``)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._seen_job_ids: set[str] = set()

    async def enqueue(self, function: str, *, job_id: str | None = None, **kwargs: object) -> None:
        if job_id is not None:
            if job_id in self._seen_job_ids:
                return
            self._seen_job_ids.add(job_id)
        self.calls.append((function, kwargs))


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover
        pytest.skip("testcontainers not installed")

    try:
        container = PostgresContainer(
            "pgvector/pgvector:pg16",
            username="veratas",
            password="veratas",
            dbname="veratas",
        )
        container.start()
    except Exception as exc:  # Docker daemon not running / image unavailable
        pytest.skip(f"Docker not available: {exc}")

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    url = f"postgresql+asyncpg://veratas:veratas@{host}:{port}/veratas"

    # Apply the real baseline migration against the container (F01 DoD).
    from app.config.settings import settings as app_settings

    # Patch BOTH URLs: migrations/env.py prefers MIGRATIONS_DATABASE_URL when set, so a
    # developer's .env pointing it at the dev DB must never leak into a test run.
    original = app_settings.DATABASE_URL
    original_migrations = app_settings.MIGRATIONS_DATABASE_URL
    app_settings.DATABASE_URL = url
    app_settings.MIGRATIONS_DATABASE_URL = url
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    try:
        command.upgrade(cfg, "head")
        yield url
    finally:
        app_settings.DATABASE_URL = original
        app_settings.MIGRATIONS_DATABASE_URL = original_migrations
        container.stop()


@pytest.fixture
async def session_factory(pg_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(pg_url)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant_engine(pg_url: str) -> AsyncIterator[None]:
    """Rebind the app-global async engine/session factory to the test container so the REAL
    ``tenant_session(org_id)`` helper (which opens from ``app.config.db.sessionmaker``,
    created at import against the dev URL) runs against the ephemeral Postgres. Restored
    after the test."""
    from app.config import db as db_mod

    engine = create_async_engine(pg_url)
    orig_engine, orig_maker = db_mod.engine, db_mod.sessionmaker
    db_mod.engine = engine
    db_mod.sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield
    finally:
        db_mod.engine, db_mod.sessionmaker = orig_engine, orig_maker
        await engine.dispose()
