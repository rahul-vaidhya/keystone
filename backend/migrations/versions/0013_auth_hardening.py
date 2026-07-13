"""auth: deactivation, login lockout, session revocation

Adds four columns to ``users`` supporting a round of auth hardening: ``is_active``
(soft-delete for org member removal — reversible, preserves FK-referenced history in
chat/knowledge tables), ``failed_login_attempts``/``locked_until`` (per-account login
lockout per OWASP guidance), and ``token_version`` (bumped on password change to
invalidate every other issued JWT — checked against the ``tv`` claim on every
access/refresh token read).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "is_active")
