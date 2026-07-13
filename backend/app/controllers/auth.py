"""Identity HTTP handlers — thin; all logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response, status

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.middleware.deps import current_user, get_ctx, require_admin
from app.models.auth import (
    ChangePasswordRequest,
    InviteRequest,
    LoginRequest,
    OrganizationOut,
    RenameOrgRequest,
    RoleChangeRequest,
    SignupRequest,
    TokenResponse,
    User,
    UserOut,
    UserStatusRequest,
)
from app.services.auth import auth_service


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    secure = settings.ENV != "dev"
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TTL_DAYS * 86400,
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.REFRESH_COOKIE_NAME, path="/auth")


async def signup(req: SignupRequest, response: Response) -> TokenResponse:
    tokens, refresh = await auth_service.signup(req)
    _set_refresh_cookie(response, refresh)
    return tokens


async def login(req: LoginRequest, response: Response) -> TokenResponse:
    tokens, refresh = await auth_service.login(req)
    _set_refresh_cookie(response, refresh)
    return tokens


async def refresh_token(request: Request, response: Response) -> TokenResponse:
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not refresh:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    tokens, new_refresh = await auth_service.refresh(refresh)
    _set_refresh_cookie(response, new_refresh)
    return tokens


async def logout(response: Response) -> None:
    _clear_refresh_cookie(response)


async def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    return UserOut.model_validate(user)


async def invite(
    req: InviteRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> UserOut:
    return await auth_service.invite(ctx, req)


async def list_users(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[UserOut]:
    return await auth_service.list_org_users(ctx)


async def change_role(
    user_id: uuid.UUID,
    req: RoleChangeRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> UserOut:
    return await auth_service.change_role(ctx, user_id, req.role)


async def set_member_active(
    user_id: uuid.UUID,
    req: UserStatusRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> UserOut:
    return await auth_service.set_member_active(ctx, user_id, req.is_active)


async def change_password(
    req: ChangePasswordRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    response: Response,
) -> TokenResponse:
    tokens, refresh_token = await auth_service.change_password(
        ctx, req.current_password, req.new_password
    )
    _set_refresh_cookie(response, refresh_token)
    return tokens


async def get_org(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> OrganizationOut:
    return await auth_service.get_org(ctx)


async def rename_org(
    req: RenameOrgRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> OrganizationOut:
    return await auth_service.rename_org(ctx, req.org_name)
