"""API startup DB check (app/config/startup_check.py): a fresh setup with Postgres down or
migrations not applied must fail at startup with the fix in the message, not answer every
sign-up / sign-in with a bare 500."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import startup_check


async def test_passes_on_migrated_database(pg_url: str, monkeypatch) -> None:
    engine = create_async_engine(pg_url)
    monkeypatch.setattr(startup_check, "engine", engine)
    try:
        await startup_check.check_database()
    finally:
        await engine.dispose()


async def test_unmigrated_database_says_run_alembic(pg_url: str, monkeypatch) -> None:
    name = f"empty_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {name}"))
    engine = create_async_engine(pg_url.rsplit("/", 1)[0] + f"/{name}")
    monkeypatch.setattr(startup_check, "engine", engine)
    try:
        with pytest.raises(startup_check.StartupCheckFailed, match="alembic upgrade head"):
            await startup_check.check_database()
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f"DROP DATABASE {name}"))
        await admin.dispose()


async def test_unreachable_database_says_start_docker(monkeypatch) -> None:
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    monkeypatch.setattr(startup_check, "engine", engine)
    try:
        with pytest.raises(startup_check.StartupCheckFailed, match="docker compose up"):
            await startup_check.check_database()
    finally:
        await engine.dispose()
