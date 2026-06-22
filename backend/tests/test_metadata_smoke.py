"""F01/F02 smoke: the baseline ORM models register on the shared Base.metadata, and every
tenant-scoped table carries org_id directly (no scope-via-parent)."""

from __future__ import annotations

import app.identity.models  # noqa: F401  (registers tables on Base.metadata)
from app.platform.db import Base

# `organizations` IS the tenancy root — it keys on `id`, so it is the only table exempt
# from carrying an `org_id` column. Every other table registered on Base.metadata is
# tenant-scoped and must carry `org_id` directly (the rule that join/child tables added in
# later phases — document_tags, knowledge_base_documents, messages, message_traces — also
# obey; this guard fails the moment one is added without it).
_TENANCY_ROOT = "organizations"


def test_baseline_tables_registered() -> None:
    assert {"organizations", "users"} <= set(Base.metadata.tables)


def test_users_carry_org_id() -> None:
    users = Base.metadata.tables["users"]
    assert "org_id" in users.columns


def test_every_tenant_scoped_table_carries_org_id() -> None:
    missing = [
        name
        for name, table in Base.metadata.tables.items()
        if name != _TENANCY_ROOT and "org_id" not in table.columns
    ]
    assert not missing, f"tenant-scoped tables missing org_id: {missing}"
