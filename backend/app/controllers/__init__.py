"""All FastAPI routers (thin HTTP handlers), re-exported from one place."""

from __future__ import annotations

from app.controllers.auth import router as auth_router
from app.controllers.chat import router as chat_router
from app.controllers.deps import current_user, get_ctx, get_db_session, require_admin
from app.controllers.documents import router as documents_router
from app.controllers.ingestion import router as ingestion_router
from app.controllers.notebooks import router as notebooks_router
from app.controllers.retrieval import router as retrieval_router

__all__ = [
    "auth_router",
    "chat_router",
    "current_user",
    "get_ctx",
    "get_db_session",
    "require_admin",
    "documents_router",
    "ingestion_router",
    "notebooks_router",
    "retrieval_router",
]
