"""Access Role domain models and Pydantic schemas (docs/access-roles-dnd-plan.md).

An Access Role is deliberately separate from the system role (``User.role`` —
owner/admin/member, DB CHECK-constrained in migration 0003, governs invites/role
changes). An Access Role is purely about resource visibility: an org owner/admin
creates one, grants it tags, and assigns members to it. A tag becomes
"access-controlling" the moment any Access Role is granted it — see
``resolve_allowed_documents`` in ``services/retrieval.py`` for how that's consumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base


class AccessRole(Base):
    __tablename__ = "access_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserAccessRole(Base):
    __tablename__ = "user_access_roles"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    access_role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("access_roles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class AccessRoleTag(Base):
    __tablename__ = "access_role_tags"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("access_roles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


# ---- API schemas ----


class AccessRoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AccessRoleOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    user_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}
