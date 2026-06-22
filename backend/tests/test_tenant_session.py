"""F02 — the ``tenant_session`` GUC plumbing, against a real Postgres.

Proves the flag semantics that the RLS backstop (Phase 6) will rely on:
- ``RLS_ENABLED`` OFF (MVP default): the ``app.org_id`` GUC is NEVER set.
- ``RLS_ENABLED`` ON: it is set inside the transaction via ``SET LOCAL`` (which Postgres
  scopes to the transaction, so it cannot leak across a pooled-connection checkout).

Skipped when Docker is unavailable (see conftest).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import TextClause, text

from app.platform import config as config_mod
from app.platform.db import tenant_session


def _read_guc() -> TextClause:
    # missing_ok=true → unset GUC returns NULL rather than erroring.
    return text("SELECT current_setting('app.org_id', true)")


async def test_guc_unset_when_flag_off(tenant_engine: None) -> None:
    org_id = uuid.uuid4()
    assert config_mod.settings.RLS_ENABLED is False  # MVP default
    async with tenant_session(org_id) as session:
        got = await session.scalar(_read_guc())
    # Unset GUC reads back as NULL or empty string — never the org_id.
    assert not got


async def test_guc_set_and_transaction_scoped_when_flag_on(
    tenant_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_mod.settings, "RLS_ENABLED", True)
    org_id = uuid.uuid4()

    async with tenant_session(org_id) as session:
        got = await session.scalar(_read_guc())
    assert got == str(org_id)  # SET LOCAL applied inside the transaction
