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
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
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
    # Optional real display name (migration 0018) — collected at signup, never guessed
    # from the email. Null for every pre-migration user and every invited member (name
    # collection isn't wired into the invite flow yet); the frontend falls back to an
    # email-derived heuristic display name when this is null.
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # enum: 'owner' | 'admin' | 'member' (DB check constraint in migration 0003)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="member")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-delete for org member removal — reversible, preserves FK-referenced history
    # (chat.messages/knowledge_bases SET NULL on hard delete; access_roles CASCADE).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bumped on password change; embedded in every issued JWT's "tv" claim and checked
    # on every current_user/refresh read — the mechanism that invalidates every OTHER
    # session the instant a password changes.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InviteToken(Base):
    """A one-time, hashed capability token for a self-serve invite link (see
    migrations/0016). ``token_hash`` is a sha256 hex digest — the raw token is never
    persisted, only ever returned once (``InviteOut.invite_token``) at invite time."""

    __tablename__ = "invite_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    org_name: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    org_id: uuid.UUID | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "member"


class AcceptInviteRequest(BaseModel):
    # ``org_id`` is a routing identifier carried by the invite link, not a secret — the
    # raw ``token`` is the actual capability. This lets accept-invite use the ordinary
    # ``tenant_session(org_id)`` instead of a second pre-tenant bootstrap system.
    org_id: uuid.UUID
    token: str
    password: str = Field(min_length=8, max_length=128)


class RoleChangeRequest(BaseModel):
    role: str


class UserStatusRequest(BaseModel):
    is_active: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


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
    name: str | None = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class InviteOut(BaseModel):
    """``invite_token`` is the RAW token — the ONLY time it is ever returned in
    plaintext. Never persisted (only its sha256 hash is), never logged."""

    user: UserOut
    org_id: uuid.UUID
    invite_token: str


class OrgChoice(BaseModel):
    org_id: uuid.UUID
    org_name: str


class LoginAmbiguousResponse(BaseModel):
    detail: str = "Email exists in multiple organizations — pick one."
    org_choices: list[OrgChoice]
