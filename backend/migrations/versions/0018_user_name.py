"""users.name: collect a real display name instead of guessing one from the email

UX audit finding: "'Welcome, Uxreview' — display names are guessed from the email" —
signup never asked for a name, so the home page capitalized whatever's left of the
``@`` in the user's email, which reads oddly for anything like ``j.smith23@company.com``.
This migration adds a real, optional ``name`` column so a genuine display name can be
collected at signup and used instead of a guess.

Nullable, and left optional at signup (not required): every existing user has no
recorded name (this column didn't exist when they signed up), and making the field
required would be a disruptive UX regression for a polish fix. Invited members are not
asked for a name either (out of scope this round — a future "edit profile" feature can
fill it in); their ``name`` simply stays null until then, same as any pre-migration user.

This is a new COLUMN on an ALREADY-RLS-protected table (``users`` is in 0015's
``_RLS_TABLES`` and already carries ``ENABLE``/``FORCE ROW LEVEL SECURITY`` + a
``tenant_isolation`` policy keyed on the table's own ``org_id`` column). No RLS
statements in this migration, by design — same reasoning as 0017_document_uploader.py's
docstring: only a brand-new TABLE needs its own policy/grant; an added column on an
existing tenant table inherits the table-level policy automatically.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "name")
