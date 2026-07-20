"""documents.uploaded_by: record which user uploaded a document

UX audit finding: the document table told the user almost nothing about a document
(no upload date/uploader, byte_size/page_count/created_at existed but were never
surfaced by the frontend). This migration adds the one genuinely-missing piece of
data — WHO uploaded a document — as a nullable FK to ``users.id``.

Nullable because every existing row has no recorded uploader (this column didn't
exist when they were created); ``ON DELETE SET NULL`` mirrors ``knowledge_bases.
created_by``'s exact precedent (``app/models/knowledge.py`` ``Notebook.created_by``)
— deleting (deactivating is the norm; hard-delete is rare/admin-only) a user must
never delete their historical documents, it should just forget who uploaded them.

This is a new COLUMN on an ALREADY-RLS-protected table (``documents`` is in 0015's
``_RLS_TABLES`` and carries ``ENABLE``/``FORCE ROW LEVEL SECURITY`` + a
``tenant_isolation`` policy keyed on the table's own ``org_id`` column already —
per 0015's own docstring, only a brand-new TABLE needs its own policy/grant; an
added column on an existing tenant table inherits the table-level policy
automatically). No RLS statements in this migration, by design — see
0016_invite_tokens.py's docstring for the contrast (that one WAS a new table and
did need its own policy).

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_documents_uploaded_by", "documents", ["uploaded_by"])


def downgrade() -> None:
    op.drop_index("ix_documents_uploaded_by", table_name="documents")
    op.drop_column("documents", "uploaded_by")
