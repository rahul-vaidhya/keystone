"""notebook_shares: per-person notebook privacy (direct ask, not a buildplan item)

Notebooks are now private to their creator by default: ``Notebook.created_by`` (already
populated on every create since migration 0008) becomes the sole visibility anchor,
with **no owner/admin bypass** — the one place in this codebase where the org system
role owner/admin does NOT see everything, per direct instruction (admins keep full
*document* access via the unchanged Access Roles system; this is notebook-level only).

A creator grants view+chat access to specific org members one at a time — deliberately
a direct per-person share, not routed through the existing Access Role group system
(a notebook is a single resource, not a class of tagged resources spanning many
documents/folders). Shared users can view the notebook and chat against it; they
cannot rename/delete it, attach/detach documents, or manage its shares — only the
creator can.

This is a new tenant-scoped table (the first since 0019's ``widgets``), so per F60's
own rule it ships its own ``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation``
policy (the load-bearing ``NULLIF`` cast) + ``app_user`` grant, copying 0019's pattern.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notebook_shares",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "shared_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_notebook_shares_org_id", "notebook_shares", ["org_id"])
    op.create_index("ix_notebook_shares_user_id", "notebook_shares", ["user_id"])

    # --- RLS: same pattern as 0015/0016/0019, applied to this one new table. ---
    op.execute("ALTER TABLE notebook_shares ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notebook_shares FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notebook_shares")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON notebook_shares
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notebook_shares TO app_user")


def downgrade() -> None:
    op.execute("REVOKE ALL ON notebook_shares FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notebook_shares")
    op.execute("ALTER TABLE notebook_shares NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notebook_shares DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_notebook_shares_user_id", table_name="notebook_shares")
    op.drop_index("ix_notebook_shares_org_id", table_name="notebook_shares")
    op.drop_table("notebook_shares")
