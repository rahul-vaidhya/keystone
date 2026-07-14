"""The ``tenant_session``/``auth_session`` GUC plumbing, against a real Postgres.

F02 originally proved flag-gated semantics (GUC set only when ``RLS_ENABLED``). F60 made
the GUC unconditional — RLS enforcement must never depend on config — so this now proves:
- ``tenant_session`` ALWAYS sets ``app.org_id`` inside its transaction;
- the setting is transaction-local: a following transaction on the same (pooled) engine
  reads it back as NULL or '' — never a leaked org_id (the RLS policies' NULLIF guard
  turns both into zero rows);
- ``auth_session`` does the same for ``app.auth_email``.

Skipped when Docker is unavailable (see conftest).
"""

from __future__ import annotations

import uuid

from sqlalchemy import TextClause, text

from app.config.db import auth_session, tenant_session


def _read_guc(name: str = "app.org_id") -> TextClause:
    # missing_ok=true → unset GUC returns NULL rather than erroring.
    return text(f"SELECT current_setting('{name}', true)")


async def test_guc_always_set_inside_transaction(tenant_engine: None) -> None:
    org_id = uuid.uuid4()
    async with tenant_session(org_id) as session:
        got = await session.scalar(_read_guc())
    assert got == str(org_id)  # set transaction-locally, unconditionally (F60)


async def test_guc_is_transaction_scoped_never_leaks(tenant_engine: None) -> None:
    org_id = uuid.uuid4()
    async with tenant_session(org_id) as session:
        assert await session.scalar(_read_guc()) == str(org_id)

    # A later transaction (potentially the same pooled connection) must never inherit
    # the previous tenant: the GUC reads back NULL or '' — both fail-closed under the
    # policies' NULLIF(current_setting(...), '')::uuid guard.
    async with tenant_session(uuid.uuid4()) as session:
        pass
    from app.config import db as db_mod

    async with db_mod.sessionmaker() as session:
        got = await session.scalar(_read_guc())
    assert not got


async def test_auth_session_sets_auth_email_guc(tenant_engine: None) -> None:
    async with auth_session("guc-probe@test.com") as session:
        got = await session.scalar(_read_guc("app.auth_email"))
        assert got == "guc-probe@test.com"
        # And the tenant GUC stays unset — the bootstrap session is pre-tenant.
        org_guc = await session.scalar(_read_guc())
        assert not org_guc
