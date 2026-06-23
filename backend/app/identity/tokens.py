"""JWT access + refresh token helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.identity.constants import TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH
from app.platform.config import settings


class TokenError(Exception):
    """Invalid or expired token."""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _encode(payload: dict[str, Any], expires_delta: timedelta) -> str:
    now = _now()
    body = {
        **payload,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(body, settings.JWT_SECRET, algorithm="HS256")


def _decode(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc


def issue_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, role: str, email: str) -> str:
    return _encode(
        {
            "sub": str(user_id),
            "org_id": str(org_id),
            "role": role,
            "email": email,
            "type": TOKEN_TYPE_ACCESS,
        },
        timedelta(minutes=settings.JWT_ACCESS_TTL_MINUTES),
    )


def issue_refresh_token(*, user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    return _encode(
        {
            "sub": str(user_id),
            "org_id": str(org_id),
            "type": TOKEN_TYPE_REFRESH,
        },
        timedelta(days=settings.JWT_REFRESH_TTL_DAYS),
    )


def decode_access_token(token: str) -> dict[str, Any]:
    payload = _decode(token)
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenError("Not an access token")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    payload = _decode(token)
    if payload.get("type") != TOKEN_TYPE_REFRESH:
        raise TokenError("Not a refresh token")
    return payload
