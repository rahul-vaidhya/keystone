"""Global HTTP exception mapping."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.chat.exceptions import GenerationFailed
from app.documents.exceptions import (
    DocumentNotFound,
    DocumentsError,
    FolderNameConflict,
    FolderNotEmpty,
    FolderNotFound,
    TagNotFound,
)
from app.identity.exceptions import (
    AmbiguousLogin,
    AuthError,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    TargetUserNotFound,
)
from app.identity.schemas import LoginAmbiguousResponse, OrgChoice
from app.knowledge.exceptions import KnowledgeError, NotebookNotFound


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

    @app.exception_handler(GenerationFailed)
    async def _generation_failed(_request: Request, exc: GenerationFailed) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The assistant is temporarily unavailable, please try again."},
        )

    @app.exception_handler(AuthError)
    async def _auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) or "Auth error"},
        )
