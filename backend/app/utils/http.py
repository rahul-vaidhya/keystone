"""Global HTTP exception mapping + shared request helpers."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.models.auth import LoginAmbiguousResponse, OrgChoice
from app.services.access_roles import (
    AccessRoleNameConflict,
    AccessRoleNotFound,
    AccessRolesError,
)
from app.services.auth import (
    AccountLocked,
    AmbiguousLogin,
    AuthError,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    InvalidInviteToken,
    TargetUserNotFound,
)
from app.services.chat import (
    FeedbackOnUserMessage,
    GenerationFailed,
    MessageNotFound,
    MessageTraceNotFound,
)
from app.services.documents import (
    DocumentNotFound,
    DocumentsError,
    FolderAccessDenied,
    FolderNameConflict,
    FolderNotEmpty,
    FolderNotFound,
    TagNotFound,
)
from app.services.embed import EmbedError, OriginNotAllowed, WidgetNotFound, WidgetRateLimited
from app.services.knowledge import KnowledgeError, NotebookAccessDenied, NotebookNotFound


def get_client_ip(request: Request) -> str:
    """Real visitor IP for rate limiting, correct whether or not this app runs behind
    a reverse proxy/load balancer. ``request.client.host`` alone is WRONG in any
    deployment fronted by a proxy (nginx, a load balancer, a CDN) — it would be the
    proxy's own address for every request, collapsing a per-IP limit into one shared
    bucket for every real visitor behind it.

    Only trusts ``X-Forwarded-For`` when the DIRECT peer (``request.client.host``) is
    itself one of ``settings.TRUSTED_PROXY_IPS`` — a client that connects directly and
    forges that header (trivial — it's just an HTTP header) is never trusted, since
    its peer address won't be in the configured list. Reads right-to-left (nearest hop
    first) and returns the first entry that isn't itself a trusted proxy — the
    standard algorithm for a chain of one or more trusted hops (mirrors what
    ``uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware`` does).
    """
    direct_ip = request.client.host if request.client else "unknown"
    trusted = {ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()}
    if not trusted or direct_ip not in trusted:
        return direct_ip
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return direct_ip
    hops = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
    for ip in reversed(hops):
        if ip not in trusted:
            return ip
    return direct_ip


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FolderNotFound)
    async def _folder_not_found(_request: Request, exc: FolderNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Folder not found"},
        )

    @app.exception_handler(TagNotFound)
    async def _tag_not_found(_request: Request, exc: TagNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Tag not found"},
        )

    @app.exception_handler(DocumentNotFound)
    async def _document_not_found(_request: Request, exc: DocumentNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Document not found"},
        )

    @app.exception_handler(FolderNotEmpty)
    async def _folder_not_empty(_request: Request, exc: FolderNotEmpty) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) or "Folder is not empty"},
        )

    @app.exception_handler(FolderNameConflict)
    async def _folder_name_conflict(_request: Request, exc: FolderNameConflict) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) or "Folder name conflict"},
        )

    @app.exception_handler(FolderAccessDenied)
    async def _folder_access_denied(_request: Request, exc: FolderAccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc) or "You do not have access to this folder"},
        )

    @app.exception_handler(DocumentsError)
    async def _documents_error(_request: Request, exc: DocumentsError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Documents error"},
        )

    @app.exception_handler(NotebookNotFound)
    async def _notebook_not_found(_request: Request, exc: NotebookNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Notebook not found"},
        )

    @app.exception_handler(NotebookAccessDenied)
    async def _notebook_access_denied(_request: Request, exc: NotebookAccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc) or "You do not have access to this notebook"},
        )

    @app.exception_handler(KnowledgeError)
    async def _knowledge_error(_request: Request, exc: KnowledgeError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Knowledge error"},
        )

    @app.exception_handler(InvalidCredentials)
    async def _invalid_credentials(_request: Request, exc: InvalidCredentials) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc) or "Invalid credentials"},
        )

    @app.exception_handler(InvalidInviteToken)
    async def _invalid_invite_token(_request: Request, exc: InvalidInviteToken) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Invalid invite link"},
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

    @app.exception_handler(AccountLocked)
    async def _account_locked(_request: Request, exc: AccountLocked) -> JSONResponse:
        remaining_minutes = max(
            1, math.ceil((exc.locked_until - datetime.now(UTC)).total_seconds() / 60)
        )
        return JSONResponse(
            status_code=status.HTTP_423_LOCKED,
            content={
                "detail": f"Account temporarily locked. Try again in {remaining_minutes} minute(s)."
            },
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

    @app.exception_handler(GenerationFailed)
    async def _generation_failed(_request: Request, exc: GenerationFailed) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The assistant is temporarily unavailable, please try again."},
        )

    @app.exception_handler(MessageTraceNotFound)
    async def _message_trace_not_found(
        _request: Request, exc: MessageTraceNotFound
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Trace not found"},
        )

    @app.exception_handler(MessageNotFound)
    async def _message_not_found(_request: Request, exc: MessageNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Message not found"},
        )

    @app.exception_handler(FeedbackOnUserMessage)
    async def _feedback_on_user_message(
        _request: Request, exc: FeedbackOnUserMessage
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Only assistant messages can be rated"},
        )

    @app.exception_handler(AuthError)
    async def _auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Auth error"},
        )

    @app.exception_handler(AccessRoleNotFound)
    async def _access_role_not_found(_request: Request, exc: AccessRoleNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Access Role not found"},
        )

    @app.exception_handler(AccessRoleNameConflict)
    async def _access_role_name_conflict(
        _request: Request, exc: AccessRoleNameConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) or "Access Role name conflict"},
        )

    @app.exception_handler(AccessRolesError)
    async def _access_roles_error(_request: Request, exc: AccessRolesError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Access Roles error"},
        )

    @app.exception_handler(WidgetNotFound)
    async def _widget_not_found(_request: Request, exc: WidgetNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) or "Widget not found"},
        )

    @app.exception_handler(OriginNotAllowed)
    async def _origin_not_allowed(_request: Request, exc: OriginNotAllowed) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc) or "Origin not allowed"},
        )

    @app.exception_handler(WidgetRateLimited)
    async def _widget_rate_limited(_request: Request, exc: WidgetRateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc) or "Rate limit exceeded"},
        )

    @app.exception_handler(EmbedError)
    async def _embed_error(_request: Request, exc: EmbedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Embed error"},
        )
