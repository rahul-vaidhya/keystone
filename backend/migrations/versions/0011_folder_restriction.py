"""documents: folder access restriction flag

Adds ``folders.restricted`` (bool, default false) — the single new column backing the
folder-based access-restriction feature (see docs/document-delete-folder-restriction-plan.md).
A restricted folder (or any folder with a restricted ancestor) is invisible to
``member``-role users in chat/retrieval; ``owner``/``admin`` always bypass. Default false
means every existing folder is unrestricted until an admin opts one in — zero behavior
change on migrate.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "folders",
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("folders", "restricted")
