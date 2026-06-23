"""FastAPI dependencies for identity / tenancy."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.constants import ADMIN_ROLES
from app.identity.exceptions import Forbidden
from app.identity.models import User
from app.identity.repository import AuthRepository
from app.identity.tokens import TokenError, decode_access_token
from app.platform import db as db_mod
from app.platform.context import TenantContext

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
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_ctx(user: Annotated[User, Depends(current_user)]) -> TenantContext:
    return TenantContext(org_id=user.org_id, user_id=user.id, role=user.role)


def require_admin(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> TenantContext:
    if ctx.role not in ADMIN_ROLES:
        raise Forbidden("Admin access required")
    return ctx
