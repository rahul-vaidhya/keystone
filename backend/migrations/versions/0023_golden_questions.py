"""golden_questions: admin-curatable golden-eval question set

New evals domain (app/models/evals.py, app/services/evals.py). A ``GoldenQuestion``
row is grown from a real graded ``/chat/ask`` answer via the admin Debug panel's
"Add to golden set" button (``POST /evals/golden-questions``), never hand-typed —
``question``/``reference_answer`` are copied verbatim from the source message pair,
and ``reference_contexts`` snapshots the retrieved chunk TEXT (a jsonb list[str], NOT
chunk ids) so a golden question stays gradable by the opt-in ``pytest -m eval`` Ragas
regression suite even after its source chunks are later re-ingested or deleted.

``source_message_id`` is ``ON DELETE SET NULL`` (not CASCADE) for the same
snapshot-survives-its-origin reasoning: everything the golden question needs to be
graded is already copied onto this row, so deleting the original conversation must
never delete the golden question with it. ``notebook_id`` IS ``ON DELETE CASCADE`` —
a golden question can't be graded without a notebook to search against.

This is a new tenant-scoped table, so per F60's own rule it ships its own full
``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation`` policy (the load-bearing
``NULLIF`` cast) + ``app_user`` grant, copying 0022's pattern.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "golden_questions",
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
            "notebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("reference_contexts", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_by",
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
    op.create_index("ix_golden_questions_org_id", "golden_questions", ["org_id"])
    op.create_index("ix_golden_questions_notebook_id", "golden_questions", ["notebook_id"])

    # --- RLS: same pattern as 0015/0016/0019/0020/0022, applied to this one new table. ---
    op.execute("ALTER TABLE golden_questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE golden_questions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON golden_questions")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON golden_questions
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON golden_questions TO app_user")


def downgrade() -> None:
    op.execute("REVOKE ALL ON golden_questions FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON golden_questions")
    op.execute("ALTER TABLE golden_questions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE golden_questions DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_golden_questions_notebook_id", table_name="golden_questions")
    op.drop_index("ix_golden_questions_org_id", table_name="golden_questions")
    op.drop_table("golden_questions")
