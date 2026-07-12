"""Identity ORM models and Pydantic schemas.

ORM models: the tenancy root (organizations) and its users. These are the only tables in
the Phase 0 baseline migration — every other tenant-scoped table FKs back to
``organizations.id`` via its own ``org_id``. Auth, invites, and roles get their
behaviour in Phase 1; here we only establish the tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_users_org_email"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    # enum: 'owner' | 'admin' | 'member' (DB check constraint in migration 0003)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="member")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    org_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    org_id: uuid.UUID | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = "member"


class RoleChangeRequest(BaseModel):
    role: str


class RenameOrgRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgChoice(BaseModel):
    org_id: uuid.UUID
    org_name: str


class LoginAmbiguousResponse(BaseModel):
    detail: str = "Email exists in multiple organizations — pick one."
    org_choices: list[OrgChoice]
