"""documents: upload + checksum dedupe columns

Phase 1 / F12. ALTERs the F11 ``documents`` anchor table — adds the storage/checksum/
pipeline-status columns; does not create a new table (memory.md F11 decision).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("storage_key", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("mime_type", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("byte_size", sa.BigInteger(), nullable=True))
    op.add_column("documents", sa.Column("checksum", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("language", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("status", sa.Text(), nullable=False, server_default="UPLOADED"),
    )
    op.add_column("documents", sa.Column("failed_stage", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("error_detail", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_unique_constraint("uq_documents_org_checksum", "documents", ["org_id", "checksum"])


def downgrade() -> None:
    op.drop_constraint("uq_documents_org_checksum", "documents", type_="unique")
    op.drop_column("documents", "metadata")
    op.drop_column("documents", "error_detail")
    op.drop_column("documents", "failed_stage")
    op.drop_column("documents", "status")
    op.drop_column("documents", "language")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "checksum")
    op.drop_column("documents", "byte_size")
    op.drop_column("documents", "mime_type")
    op.drop_column("documents", "storage_key")
