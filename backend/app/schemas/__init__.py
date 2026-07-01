"""All Pydantic request/response shapes (wire layer), re-exported from one place."""

from __future__ import annotations

from app.schemas.auth import (
    InviteRequest,
    LoginAmbiguousResponse,
    LoginRequest,
    OrgChoice,
    RoleChangeRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.chat import ChatRequest, ChatResponse, ResolvedCitation
from app.schemas.documents import (
    DocumentOut,
    FolderCreate,
    FolderMove,
    FolderOut,
    FolderRename,
    TagCreate,
    TagOut,
)
from app.schemas.ingestion import ChunkHit, ChunkRecord
from app.schemas.knowledge import NotebookCreate, NotebookOut, NotebookUpdate
from app.schemas.retrieval import ContextBlock, RetrievalSearchRequest, RetrievalSearchResponse

__all__ = [
    "InviteRequest",
    "LoginAmbiguousResponse",
    "LoginRequest",
    "OrgChoice",
    "RoleChangeRequest",
    "SignupRequest",
    "TokenResponse",
    "UserOut",
    "DocumentOut",
    "FolderCreate",
    "FolderMove",
    "FolderOut",
    "FolderRename",
    "TagCreate",
    "TagOut",
    "ChunkHit",
    "ChunkRecord",
    "NotebookCreate",
    "NotebookOut",
    "NotebookUpdate",
    "ContextBlock",
    "RetrievalSearchRequest",
    "RetrievalSearchResponse",
    "ChatRequest",
    "ChatResponse",
    "ResolvedCitation",
]
