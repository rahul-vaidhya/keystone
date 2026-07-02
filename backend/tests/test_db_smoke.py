"""F01 DoD smoke: the baseline migration creates the tables and an org round-trips
through a real Postgres+pgvector. Skipped when Docker is unavailable (see conftest)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.auth import Organization


async def test_insert_and_read_org(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="Acme"))

    async with session_factory() as session:
        got = await session.scalar(select(Organization).where(Organization.id == org_id))

    assert got is not None
    assert got.name == "Acme"
    assert got.plan == "free"  # server default supplied by the migration
