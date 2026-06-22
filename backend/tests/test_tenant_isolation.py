"""F02 DoD — the app-level tenant-isolation integration test.

Two orgs, both with rows in a shared tenant-scoped table (``users``), against a REAL
Postgres+pgvector. With the always-on app-level ``WHERE org_id = :org`` filter (applied by
the base repository), org A's scoped repository reads ONLY org A's rows — zero cross-read —
even though org B's rows physically coexist in the same table. The control assertion (an
unscoped query sees both orgs' rows) proves it is the app filter, not absent data, that
does the isolating.

This is the *app-level* guarantee that MVP ships on. The restricted-role, filter-OMITTED
"teeth" test (RLS alone blocks the leak) is Phase 6 / F60, not here. Skipped when Docker is
unavailable (see conftest).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity.models import Organization, User
from app.identity.repository import UserRepository
from app.platform.context import TenantContext
from app.platform.db import tenant_session


async def _seed_two_orgs(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create org A (one user) and org B (two users) in the shared tables."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Organization(id=org_a, name="Org A"),
                Organization(id=org_b, name="Org B"),
                User(org_id=org_a, email="a1@a.test"),
                User(org_id=org_b, email="b1@b.test"),
                User(org_id=org_b, email="b2@b.test"),
            ]
        )
    return org_a, org_b


async def test_app_filter_blocks_cross_org_read(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_engine: None,
) -> None:
    org_a, org_b = await _seed_two_orgs(session_factory)

    # Org A, through the real tenant_session + base-repository app filter.
    async with tenant_session(org_a) as session:
        repo = UserRepository(session, TenantContext(org_id=org_a))
        a_users = await repo.list()

    # Org B likewise.
    async with tenant_session(org_b) as session:
        repo = UserRepository(session, TenantContext(org_id=org_b))
        b_users = await repo.list()

    a_emails = {u.email for u in a_users}
    b_emails = {u.email for u in b_users}

    # Each org sees exactly its own rows — zero cross-read.
    assert a_emails == {"a1@a.test"}
    assert b_emails == {"b1@b.test", "b2@b.test"}
    assert a_emails.isdisjoint(b_emails)

    # Control: without the filter, both orgs' rows are right there in the same table —
    # proving it is the app-level scope, not missing data, that isolates them.
    async with session_factory() as session:
        all_users = (await session.scalars(select(User))).all()
    assert {u.email for u in all_users} >= a_emails | b_emails
