"""F02 fast unit — the base repository emits the app-level org_id filter on every query.

No database: compiling the scoped statement is enough to prove the ``WHERE org_id = :org``
predicate is present by construction. The behavioural proof (zero cross-read against real
Postgres) is in test_tenant_isolation.py.
"""

from __future__ import annotations

import uuid

from app.identity.repository import UserRepository
from app.platform.context import TenantContext


def test_scoped_select_includes_org_filter() -> None:
    ctx = TenantContext(org_id=uuid.uuid4())
    repo = UserRepository(session=None, ctx=ctx)  # type: ignore[arg-type]  # no DB call here

    compiled = str(repo._scoped())

    assert "WHERE users.org_id" in compiled
