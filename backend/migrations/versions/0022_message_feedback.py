"""message_feedback: per-user rating on assistant chat messages (thumbs up/down)

Wires up the previously-dead thumbs up/down UI control (``ChatPanel.tsx`` had a
purely-local ``useState`` that reset to ``{}`` on every mount and persisted nothing).
One row per ``(message_id, user_id)`` — an UPSERT target, not an audit log of every
click; a user re-rating the same message updates their existing row rather than
inserting a second one. Dies with its message (``ON DELETE CASCADE``, same precedent
as ``message_traces``).

``reason_tags``/``comment``/``corrected_answer`` exist schema-ready for a FUTURE admin
labeling UI (not built this round) — only ``rating`` is populated by the wired-up
thumbs buttons today.

This is a new tenant-scoped table (the first since 0021, which only added a column to
an existing RLS-protected table), so per F60's own rule it ships its own full
``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation`` policy (the load-bearing
``NULLIF`` cast) + ``app_user`` grant, copying 0020's pattern.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column(
            "reason_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("corrected_answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
    )
    op.create_index("ix_message_feedback_org_id", "message_feedback", ["org_id"])

    # --- RLS: same pattern as 0015/0016/0019/0020, applied to this one new table. ---
    op.execute("ALTER TABLE message_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message_feedback FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON message_feedback")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON message_feedback
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON message_feedback TO app_user")


def downgrade() -> None:
    op.execute("REVOKE ALL ON message_feedback FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON message_feedback")
    op.execute("ALTER TABLE message_feedback NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message_feedback DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_message_feedback_org_id", table_name="message_feedback")
    op.drop_table("message_feedback")
