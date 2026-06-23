"""knowledge: notebooks (knowledge_bases + knowledge_base_documents)

Phase 3 / F30. Notebooks are a reference join, not a copy: ``knowledge_base_documents``
links a notebook to existing ``documents`` rows by id — attaching a document to N
notebooks never duplicates its chunks/embeddings (those stay keyed on ``document_id``
alone). ``org_id`` lives on the join table directly per the "no scope-via-parent"
tenancy rule.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_knowledge_bases_org_id", "knowledge_bases", ["org_id"])

    op.create_table(
        "knowledge_base_documents",
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
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("knowledge_base_id", "document_id"),
    )
    op.create_index("ix_knowledge_base_documents_org_id", "knowledge_base_documents", ["org_id"])
    op.create_index(
        "ix_knowledge_base_documents_document_id", "knowledge_base_documents", ["document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_base_documents_document_id", table_name="knowledge_base_documents")
    op.drop_index("ix_knowledge_base_documents_org_id", table_name="knowledge_base_documents")
    op.drop_table("knowledge_base_documents")

    op.drop_index("ix_knowledge_bases_org_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
