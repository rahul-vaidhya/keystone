"""Database plumbing: the shared declarative ``Base``, the async engine, and the
session factory.

Every module's ORM models inherit from this one ``Base`` so Alembic's
``target_metadata = Base.metadata`` sees the whole schema. The transaction-scoped,
tenant-aware ``tenant_session(org_id)`` helper is the only way the app and workers should
open DB work — enforced by RLS since F60: the app connects as the restricted ``app_user``
role, and a session whose transaction never set the ``app.org_id`` GUC reads zero rows
(fail-closed). The narrow ``auth_session(email)`` variant exists only for the pre-tenant
auth bootstrap (signup's global email check, login's cross-org candidate search).
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
# it is the app-wide async session factory, and the ``tenant_session``/``auth_session``
# helpers below are the ONLY things that should open sessions from it. (We import
# ``async_sessionmaker``, not ``sessionmaker``, so the shadow is in name only — there is
# no import collision.)
sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def set_org_guc(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set the transaction-local ``app.org_id`` GUC the RLS ``tenant_isolation``
    policies key on. Exposed separately from ``tenant_session`` for the one flow that
    must switch INTO a tenant scope mid-transaction: login, which starts pre-tenant
    (``auth_session``) and only learns the org once the candidate user is matched.

    We use ``set_config(name, value, is_local=true)`` (the function form of ``SET LOCAL``)
    rather than ``SET LOCAL app.org_id = :org``: ``SET`` is a utility statement that does
    NOT accept bind parameters, so only the function form can take the org_id safely-bound.
    """
    await session.execute(
        text("SELECT set_config('app.org_id', :org, true)"),
        {"org": str(org_id)},
    )


@asynccontextmanager
async def tenant_session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """The one transaction-scoped, tenant-aware session helper — used by BOTH the request
    path and arq workers (workers pass ``org_id`` from the job payload).

    Opens a transaction and sets the ``app.org_id`` GUC transaction-locally — always,
    unconditionally (F60): RLS enforcement must never depend on a config flag, and the
    ``is_local => true`` scoping means the GUC resets at transaction end so a pooled
    connection cannot leak one tenant's ``org_id`` into the next checkout. The
    repositories' app-level ``WHERE org_id = :org`` filter stays on top as the first
    line; RLS is the backstop that holds even when that filter is forgotten.
    """
    async with sessionmaker() as session, session.begin():
        await set_org_guc(session, org_id)
        yield session


@asynccontextmanager
async def auth_session(email: str) -> AsyncIterator[AsyncSession]:
    """Pre-tenant session for the auth bootstrap ONLY (signup, login) — flows that must
    read ``users`` before any org is known. Sets the transaction-local ``app.auth_email``
    GUC; the permissive RLS policies added in migration 0015 let this session read
    exactly the ``users`` rows matching that email (and, for login's AmbiguousLogin org
    names, the ``organizations`` rows those users belong to) — nothing else. Unset GUC →
    NULL → zero rows, same fail-closed shape as ``tenant_session``.

    Login switches into a normal tenant scope mid-transaction via ``set_org_guc`` once
    the candidate user is matched (the lockout-counter writes run under the ordinary
    ``tenant_isolation`` policy, not under this bootstrap policy).
    """
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.auth_email', :email, true)"),
            {"email": email},
        )
        yield session
