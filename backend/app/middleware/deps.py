"""FastAPI dependencies for identity / tenancy."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.auth import User
from app.services.auth import AuthRepository, Forbidden
from app.utils.constants import ADMIN_ROLES
from app.utils.tokens import TokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with db_mod.sessionmaker() as session:
        yield session


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_access_token(creds.credentials)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user_id = uuid.UUID(payload["sub"])
    user = await AuthRepository(session).get_user_by_id(user_id)
    # Re-checked on EVERY request (this is a DB read already, not a stateless-JWT-only
    # check) — a deactivated member or a password change (which bumps token_version)
    # takes effect on the very next call, not just on the next refresh.
    if user is None or not user.is_active or user.token_version != payload.get("tv"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_ctx(user: Annotated[User, Depends(current_user)]) -> TenantContext:
    return TenantContext(org_id=user.org_id, user_id=user.id, role=user.role)


def require_admin(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> TenantContext:
    if ctx.role not in ADMIN_ROLES:
        raise Forbidden("Admin access required")
    return ctx
