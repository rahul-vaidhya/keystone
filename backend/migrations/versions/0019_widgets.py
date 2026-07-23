"""widgets: embeddable website chatbot widget (docs/embed-widget-plan.md)

An org admin picks a notebook and creates a **widget**: one notebook + a public
capability id (``public_id``) + an origin allowlist + an active flag. Anonymous
visitors on an external site chat with that one notebook through a new public
backend endpoint, protected by the origin allowlist and Redis rate limits (app-layer,
not this migration). ``public_id`` is stored PLAINTEXT and GLOBALLY unique (not
per-org) — unlike invite tokens it is public by design (visible in the customer's
page source the moment the snippet is pasted), so hashing it at rest buys nothing;
the public URL/script carries only ``org_id`` + ``public_id`` and must resolve to
exactly one widget with no prior org context.

This is a new tenant-scoped table (the first since migration 0018), so per F60's own
rule it ships its own ``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation``
policy (the load-bearing ``NULLIF`` cast — a committed transaction-local GUC resets
to ``''``, not NULL, and a bare ``''::uuid`` cast raises) + ``app_user`` grant, copying
0016_invite_tokens.py's exact pattern.

Also adds ``conversations.widget_id`` (nullable FK to ``widgets.id``, ``ON DELETE SET
NULL``) marking widget-originated conversations — a new COLUMN on an ALREADY-RLS-
protected table (``conversations`` has carried ``ENABLE``/``FORCE ROW LEVEL SECURITY``
+ a ``tenant_isolation`` policy since migration 0015) inherits the table-level policy
automatically, so no RLS statements are needed for that column, per 0017's precedent.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widgets",
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
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # Public by design — plaintext, globally unique (see module docstring).
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column(
            "allowed_origins",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        # Set explicitly in the repository's update() method (MissingGreenlet gotcha) —
        # never onupdate=func.now(). No server-side onupdate here either.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("public_id", name="uq_widgets_public_id"),
    )
    op.create_index("ix_widgets_org_id", "widgets", ["org_id"])

    # --- RLS: same pattern as 0015/0016, applied to this one new table. ---
    op.execute("ALTER TABLE widgets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE widgets FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON widgets")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON widgets
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON widgets TO app_user")

    # --- conversations.widget_id: new column on an already-RLS-protected table, no new
    # RLS statements needed (see module docstring / 0017 precedent). ---
    op.add_column(
        "conversations",
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("widgets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_conversations_widget_id", "conversations", ["widget_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_widget_id", table_name="conversations")
    op.drop_column("conversations", "widget_id")

    op.execute("REVOKE ALL ON widgets FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON widgets")
    op.execute("ALTER TABLE widgets NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE widgets DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_widgets_org_id", table_name="widgets")
    op.drop_table("widgets")
