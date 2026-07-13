"""chat: message_traces (F42 admin debug bundle)

Persists (not recomputes) the retrieved hits+scores, the exact prompt sent to the LLM,
and the raw model output for every answer — one row per message, written in the same
transaction as its conversation+message pair. Read-only, admin-gated
(``GET /chat/messages/{message_id}/trace``, ``role in ('owner','admin')``).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_traces",
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
        sa.Column("hits", postgresql.JSONB(), nullable=False),
        sa.Column("final_prompt", sa.Text(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("message_id", name="uq_message_traces_message_id"),
    )
    op.create_index("ix_message_traces_org_id", "message_traces", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_message_traces_org_id", table_name="message_traces")
    op.drop_table("message_traces")
