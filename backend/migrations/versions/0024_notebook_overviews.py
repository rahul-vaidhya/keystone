"""notebook_overviews: on-demand, cached "gist of everything" artifact per notebook

P1 roadmap (memory.md), feature 3 of 3 (Notebook Overview). One row per notebook
(``notebook_id`` UNIQUE) — generation is an upsert-in-place, never an accumulating
history; ``app.services.knowledge.overview`` reuses the SAME map-reduce mechanism
(``app.services.retrieval.mapreduce``) feature 1's chat broad-query router already
built, over V2 enrichment section summaries, but persists the synthesized result
standalone rather than answering one chat message.

``citations`` is a jsonb list of ``ResolvedCitation``-shaped (``citation_type="section"``)
dicts, mirroring how ``messages.citations``/``golden_questions.reference_contexts``
already store citation-shaped jsonb elsewhere in this schema. ``generated_by`` is
``ON DELETE SET NULL`` (same precedent as ``notebook_shares.shared_by``/
``widgets.created_by``) — deleting the generating user must never delete the cached
overview. ``stale`` defaults false and is flipped true (never auto-cleared except by a
fresh regenerate) whenever the notebook's document set changes (attach/detach) — the
cached content itself is never deleted or auto-regenerated, only flagged so the
frontend can show a "this may be out of date" banner.

This is a new tenant-scoped table, so per F60's own rule it ships its own full
``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation`` policy (the load-bearing
``NULLIF`` cast) + ``app_user`` grant, copying 0023's pattern.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notebook_overviews",
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
            unique=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "generated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_document_count", sa.Integer(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_notebook_overviews_org_id", "notebook_overviews", ["org_id"])

    # --- RLS: same pattern as 0015/0016/0019/0020/0022/0023, applied to this one new table. ---
    op.execute("ALTER TABLE notebook_overviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notebook_overviews FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notebook_overviews")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON notebook_overviews
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notebook_overviews TO app_user")


def downgrade() -> None:
    op.execute("REVOKE ALL ON notebook_overviews FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notebook_overviews")
    op.execute("ALTER TABLE notebook_overviews NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notebook_overviews DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_notebook_overviews_org_id", table_name="notebook_overviews")
    op.drop_table("notebook_overviews")
