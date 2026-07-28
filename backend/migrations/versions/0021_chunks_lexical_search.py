"""chunks_lexical_search: hybrid search's lexical (full-text) candidate path

Adds a STORED generated ``tsvector`` column (``content_tsv``) to ``chunks``, computed by
Postgres itself from ``content`` on every insert/update — the app never writes to it
(``app/models/ingestion.py``'s ``Chunk.content_tsv`` is mapped read-only). Backed by a
GIN index for ``@@``/``ts_rank`` full-text queries (``ChunkRepository.
search_chunks_lexical``, hybrid search's lexical candidate path, fused with vector kNN
via Reciprocal Rank Fusion — ``fuse_rrf`` in ``app/services/retrieval.py``). Gated behind
``HYBRID_SEARCH_ENABLED`` (default ``False``) — a complete no-op until turned on.

Native Postgres full-text search only (``to_tsvector``/``websearch_to_tsquery``/
``ts_rank``) — no third-party extension (not ``pg_search``/ParadeDB), no Docker image
change.

This is a column+index addition on ``chunks``, an EXISTING RLS-protected tenant table
(``ENABLE``/``FORCE ROW LEVEL SECURITY`` + a ``tenant_isolation`` policy already applied
by migration 0015) — no new RLS policy/grant is needed here; that's only for brand-new
tables (see 0019/0020's pattern). Postgres computes ``content_tsv`` for both new AND
existing rows automatically as part of the ``ALTER TABLE ... ADD COLUMN ... GENERATED
ALWAYS AS ... STORED`` statement itself — no manual backfill script needed.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-28

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.create_index("ix_chunks_content_tsv", "chunks", ["content_tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_content_tsv", table_name="chunks")
    op.execute("ALTER TABLE chunks DROP COLUMN content_tsv")
