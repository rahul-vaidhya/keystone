"""documents: root-level folder name uniqueness backstop

``uq_folders_org_parent_name`` (``UNIQUE(org_id, parent_id, name)``, from F11/0004) gives
no protection between two ROOT-level folders sharing a name — Postgres treats
``NULL != NULL`` for uniqueness purposes, so two rows with ``parent_id IS NULL`` and the
same name never violate that constraint. This was flagged as an open finding during F25
(folder move/rename/delete) and is closed here with a partial unique index scoped to the
root case, leaving the existing constraint untouched for non-null parents.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_folders_org_root_name",
        "folders",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_folders_org_root_name", table_name="folders")
