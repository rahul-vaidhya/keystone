"""All SQLAlchemy ORM models and Pydantic schemas, re-exported from one place.

ORM models: every model module must be imported somewhere on the path to ``Base.metadata``
or its tables silently vanish from Alembic autogenerate. Import ORDER does not matter here:
every FK in this codebase is a string table-name reference (``ForeignKey("organizations.id")``),
never a direct class reference or ``relationship()``, so there is no load-time dependency
between model modules for SQLAlchemy to resolve. (ruff's isort keeps these alphabetical —
that's fine, don't fight it.)

Pydantic schemas: all request/response wire shapes are now merged into their corresponding
model modules and re-exported here alongside the ORM classes.
"""

from __future__ import annotations

from app.config.db import Base
from app.models.auth import (
    InviteRequest,
    LoginAmbiguousResponse,
    LoginRequest,
    Organization,
    OrgChoice,
    RoleChangeRequest,
    SignupRequest,
    TokenResponse,
    User,
    UserOut,
)
from app.models.chat import ChatRequest, ChatResponse, Conversation, Message, ResolvedCitation
from app.models.documents import (
    Document,
    DocumentOut,
    DocumentStatus,
    DocumentTag,
    Folder,
    FolderCreate,
    FolderMove,
    FolderOut,
    FolderRename,
    Tag,
    TagCreate,
    TagOut,
)
from app.models.ingestion import Chunk, ChunkHit, ChunkRecord, Embedding, Section
from app.models.knowledge import (
    Notebook,
    NotebookCreate,
    NotebookDocument,
    NotebookOut,
    NotebookUpdate,
)
from app.models.retrieval import ContextBlock, RetrievalSearchRequest, RetrievalSearchResponse

__all__ = [
    "Base",
    "Organization",
    "User",
    "Document",
    "DocumentStatus",
    "DocumentTag",
    "Folder",
    "Tag",
    "Chunk",
    "Embedding",
    "Section",
    "Notebook",
    "NotebookDocument",
    "Conversation",
    "Message",
    # Pydantic schemas
    "InviteRequest",
    "LoginAmbiguousResponse",
    "LoginRequest",
    "OrgChoice",
    "RoleChangeRequest",
    "SignupRequest",
    "TokenResponse",
    "UserOut",
    "ChatRequest",
    "ChatResponse",
    "ResolvedCitation",
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
]
