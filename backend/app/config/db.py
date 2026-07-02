"""Database plumbing: the shared declarative ``Base``, the async engine, and the
session factory.

Every module's ORM models inherit from this one ``Base`` so Alembic's
``target_metadata = Base.metadata`` sees the whole schema. The transaction-scoped,
tenant-aware ``tenant_session(org_id)`` helper is the only way the app and workers should
open DB work.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import settings


class Base(DeclarativeBase):
    """Declarative base shared by all module models."""


def create_engine() -> AsyncEngine:
    return create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


engine: AsyncEngine = create_engine()
# This module-level name intentionally shadows SQLAlchemy's ``sessionmaker`` factory name:
# it is the app-wide async session factory, and ``tenant_session`` below is the ONLY thing
# that should open sessions from it. (We import ``async_sessionmaker``, not ``sessionmaker``,
# so the shadow is in name only — there is no import collision.)
sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def tenant_session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """The one transaction-scoped, tenant-aware session helper — used by BOTH the request
    path and arq workers (workers pass ``org_id`` from the job payload).

    Opens a transaction and, only when ``RLS_ENABLED`` is on (Phase 6 / F60; OFF in MVP
    dev/test), sets the ``app.org_id`` GUC transaction-locally so it resets at transaction
    end and a pooled connection cannot leak one tenant's ``org_id`` into the next checkout.
    Regardless of the flag, repositories ALWAYS apply ``WHERE org_id = :org`` — that
    app-level filter is the MVP guarantee; RLS is the backstop.

    We use ``set_config(name, value, is_local=true)`` (the function form of ``SET LOCAL``)
    rather than ``SET LOCAL app.org_id = :org``: ``SET`` is a utility statement that does NOT
    accept bind parameters, so only the function form can take the org_id safely-bound.
    """
    async with sessionmaker() as session, session.begin():
        if settings.RLS_ENABLED:  # OFF in MVP dev/test; F60 turns it on
            await session.execute(
                text("SELECT set_config('app.org_id', :org, true)"),
                {"org": str(org_id)},
            )
        yield session
