"""documents: access roles (tag-based RBAC), replacing folders.restricted

Adds the Access Role system (docs/access-roles-dnd-plan.md): ``access_roles`` (org-owned,
named), ``user_access_roles`` (which members hold which roles), ``access_role_tags``
(which tags a role grants — a tag becomes "access-controlling" the moment it's granted to
any role), and ``folder_tags`` (mirrors ``document_tags`` — folders can now carry tags,
inherited down the subtree by ``resolve_allowed_documents``). This fully supersedes the
``folders.restricted`` boolean from migration 0011 (dropped here) — pre-production
software, no real customer data at stake in dropping it.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_roles",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_access_roles_org_name"),
    )
    op.create_index("ix_access_roles_org_id", "access_roles", ["org_id"])

    op.create_table(
        "user_access_roles",
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
        sa.Column(
            "access_role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("access_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "access_role_id"),
    )
    op.create_index("ix_user_access_roles_org_id", "user_access_roles", ["org_id"])
    op.create_index("ix_user_access_roles_access_role_id", "user_access_roles", ["access_role_id"])

    op.create_table(
        "access_role_tags",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "access_role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("access_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("access_role_id", "tag_id"),
    )
    op.create_index("ix_access_role_tags_org_id", "access_role_tags", ["org_id"])
    op.create_index("ix_access_role_tags_tag_id", "access_role_tags", ["tag_id"])

    op.create_table(
        "folder_tags",
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("folder_id", "tag_id"),
    )
    op.create_index("ix_folder_tags_org_id", "folder_tags", ["org_id"])
    op.create_index("ix_folder_tags_tag_id", "folder_tags", ["tag_id"])

    op.drop_column("folders", "restricted")


def downgrade() -> None:
    op.add_column(
        "folders",
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.drop_index("ix_folder_tags_tag_id", table_name="folder_tags")
    op.drop_index("ix_folder_tags_org_id", table_name="folder_tags")
    op.drop_table("folder_tags")

    op.drop_index("ix_access_role_tags_tag_id", table_name="access_role_tags")
    op.drop_index("ix_access_role_tags_org_id", table_name="access_role_tags")
    op.drop_table("access_role_tags")

    op.drop_index("ix_user_access_roles_access_role_id", table_name="user_access_roles")
    op.drop_index("ix_user_access_roles_org_id", table_name="user_access_roles")
    op.drop_table("user_access_roles")

    op.drop_index("ix_access_roles_org_id", table_name="access_roles")
    op.drop_table("access_roles")
