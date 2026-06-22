"""F01 smoke: the baseline ORM models register on the shared Base.metadata, and every
tenant-scoped table carries org_id directly (no scope-via-parent)."""

from __future__ import annotations

import app.identity.models  # noqa: F401  (registers tables on Base.metadata)
from app.platform.db import Base


def test_baseline_tables_registered() -> None:
    assert {"organizations", "users"} <= set(Base.metadata.tables)


def test_users_carry_org_id() -> None:
    users = Base.metadata.tables["users"]
    assert "org_id" in users.columns
