"""messages.claim_checks: per-sentence citation-check results

Nullable jsonb list of ``ClaimCheck``-shaped dicts (``app.services.chat.citation_check``),
written only when ``CITATION_CHECK_ENABLED`` was on for the answering turn. This is a
column on an existing table that already has ENABLE/FORCE RLS + the ``tenant_isolation``
policy (migration 0015) and the ``app_user`` grant — row-level policies and table grants
cover new columns, so no new policy or grant is needed.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("claim_checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "claim_checks")
