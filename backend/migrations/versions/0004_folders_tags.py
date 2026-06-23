"""documents: folders, tags, document_tags, minimal documents

Phase 1 / F11. Folder tree (materialized path) + tags for document organization.
``documents`` lands here as a minimal anchor table (id/org_id/folder_id/title) so
folders/tags have something to attach to; F12 (upload + dedupe) ALTERs it to add the
storage/checksum/status/pipeline columns — this migration does not pre-build them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folders",
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
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "parent_id", "name", name="uq_folders_org_parent_name"),
    )
    op.create_index("ix_folders_org_id", "folders", ["org_id"])
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    op.create_table(
        "tags",
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_tags_org_name"),
    )
    op.create_index("ix_tags_org_id", "tags", ["org_id"])

    op.create_table(
        "documents",
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
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_org_id", "documents", ["org_id"])
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])

    op.create_table(
        "document_tags",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("document_id", "tag_id"),
    )
    op.create_index("ix_document_tags_org_id", "document_tags", ["org_id"])
    op.create_index("ix_document_tags_tag_id", "document_tags", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_document_tags_tag_id", table_name="document_tags")
    op.drop_index("ix_document_tags_org_id", table_name="document_tags")
    op.drop_table("document_tags")

    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_index("ix_documents_org_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_tags_org_id", table_name="tags")
    op.drop_table("tags")

    op.drop_index("ix_folders_parent_id", table_name="folders")
    op.drop_index("ix_folders_org_id", table_name="folders")
    op.drop_table("folders")
