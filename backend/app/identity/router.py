"""Identity HTTP routes — thin; all logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.identity.deps import current_user, get_ctx, require_admin
from app.identity.models import User
from app.identity.schemas import (
    InviteRequest,
    LoginRequest,
    RoleChangeRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.identity.service import auth_service
from app.platform.config import settings
from app.platform.context import TenantContext

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest, response: Response) -> TokenResponse:
    tokens, refresh = await auth_service.signup(req)
    _set_refresh_cookie(response, refresh)
    return tokens


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, response: Response) -> TokenResponse:
    tokens, refresh = await auth_service.login(req)
    _set_refresh_cookie(response, refresh)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request, response: Response) -> TokenResponse:
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not refresh:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    tokens, new_refresh = await auth_service.refresh(refresh)
    _set_refresh_cookie(response, new_refresh)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/invite", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def invite(
    req: InviteRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> UserOut:
    return await auth_service.invite(ctx, req)


@router.get("/users", response_model=list[UserOut])
async def list_users(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[UserOut]:
    return await auth_service.list_org_users(ctx)


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def change_role(
    user_id: uuid.UUID,
    req: RoleChangeRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> UserOut:
    return await auth_service.change_role(ctx, user_id, req.role)
