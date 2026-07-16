"""invite_tokens: self-serve one-time invite links (replaces admin-typed initial password)

Previously ``POST /auth/invite`` required the inviting owner/admin to type the new
teammate's initial password themselves — no link, no email, the password had to be
relayed out-of-band and the admin ended up knowing it. This migration adds
``invite_tokens``: invite now creates the user with ``password_hash=NULL`` and a
one-time, hashed (sha256, never the raw token) capability token with a 7-day expiry.
The invitee visits a link carrying ``org_id`` (a routing identifier, not a secret) +
the raw token, sets their OWN password via ``POST /auth/accept-invite``, and is logged
straight in. ``User.password_hash`` was already nullable (F60-era column); ``login()``'s
existing ``if not user.password_hash`` branch already rejects login for a
not-yet-accepted invite, unchanged by this migration.

This is the FIRST new tenant-scoped table since F60 locked enforced RLS (migration
0015) — per that migration's own docstring, every future tenant table ships its own
``ENABLE``/``FORCE ROW LEVEL SECURITY`` + ``tenant_isolation`` policy + ``app_user``
grant in its own migration. The policy predicate copies 0015's exact shape, including
the load-bearing ``NULLIF`` (a committed transaction-local GUC resets to ``''``, not
NULL, and a bare ``''::uuid`` cast raises instead of matching nothing).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_tokens",
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
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256 hex digest only — the raw token is never persisted, only ever returned
        # once in InviteOut at invite time.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("token_hash", name="uq_invite_tokens_token_hash"),
    )
    op.create_index("ix_invite_tokens_org_id", "invite_tokens", ["org_id"])
    op.create_index("ix_invite_tokens_user_id", "invite_tokens", ["user_id"])

    # --- RLS: same pattern as 0015, applied to this one new table. ---
    op.execute("ALTER TABLE invite_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invite_tokens FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invite_tokens")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON invite_tokens
            USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invite_tokens TO app_user")


def downgrade() -> None:
    op.execute("REVOKE ALL ON invite_tokens FROM app_user")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invite_tokens")
    op.execute("ALTER TABLE invite_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invite_tokens DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_invite_tokens_user_id", table_name="invite_tokens")
    op.drop_index("ix_invite_tokens_org_id", table_name="invite_tokens")
    op.drop_table("invite_tokens")
