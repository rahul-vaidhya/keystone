"""Identity Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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
