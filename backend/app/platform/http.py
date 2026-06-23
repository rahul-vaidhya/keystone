"""Global HTTP exception mapping."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.identity.exceptions import (
    AmbiguousLogin,
    AuthError,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    TargetUserNotFound,
)
from app.identity.schemas import LoginAmbiguousResponse, OrgChoice


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidCredentials)
    async def _invalid_credentials(_request: Request, exc: InvalidCredentials) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc) or "Invalid credentials"},
        )

    @app.exception_handler(EmailTaken)
    async def _email_taken(_request: Request, exc: EmailTaken) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) or "Email already registered"},
        )

    @app.exception_handler(Forbidden)
    async def _forbidden(_request: Request, exc: Forbidden) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc) or "Forbidden"},
        )

    @app.exception_handler(TargetUserNotFound)
    async def _target_user_not_found(_request: Request, exc: TargetUserNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "User not found"},
        )

    @app.exception_handler(AmbiguousLogin)
    async def _ambiguous_login(_request: Request, exc: AmbiguousLogin) -> JSONResponse:
        body = LoginAmbiguousResponse(
            org_choices=[
                OrgChoice(org_id=item["org_id"], org_name=item["org_name"])
                for item in exc.org_choices
            ]
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(AuthError)
    async def _auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Auth error"},
        )
