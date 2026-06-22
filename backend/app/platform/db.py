"""Database plumbing: the shared declarative ``Base``, the async engine, and the
session factory.

Every module's ORM models inherit from this one ``Base`` so Alembic's
``target_metadata = Base.metadata`` sees the whole schema. The transaction-scoped,
tenant-aware ``tenant_session(org_id)`` helper is added in F02 and is the only way the
app and workers should open DB work.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.platform.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by all module models."""


def create_engine() -> AsyncEngine:
    return create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


engine: AsyncEngine = create_engine()
sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
