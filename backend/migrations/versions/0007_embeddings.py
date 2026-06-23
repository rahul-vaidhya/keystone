"""ingestion: embeddings (embedding stage)

Phase 2 / F22. Polymorphic, multi-granularity vector index (architecture.md
"embeddings"). Owned by ``ingestion`` — same reasoning as sections/chunks (F21): it's
the stage that produces the rows, no ``retrieval`` module exists yet to own the table.
Only ``owner_type='chunk'`` rows are inserted by F22; ``unique(owner_type, owner_id,
model)`` is the idempotent-re-embed constraint.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-26

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "embeddings",
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
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_type", sa.Text(), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("owner_type", "owner_id", "model", name="uq_embeddings_owner_model"),
    )
    op.create_index("ix_embeddings_org_id", "embeddings", ["org_id"])
    op.create_index("ix_embeddings_document_id", "embeddings", ["document_id"])
    op.create_index(
        "ix_embeddings_org_document_owner", "embeddings", ["org_id", "document_id", "owner_type"]
    )
    op.execute(
        "CREATE INDEX ix_embeddings_embedding_hnsw ON embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_embedding_hnsw")
    op.drop_index("ix_embeddings_org_document_owner", table_name="embeddings")
    op.drop_index("ix_embeddings_document_id", table_name="embeddings")
    op.drop_index("ix_embeddings_org_id", table_name="embeddings")
    op.drop_table("embeddings")
