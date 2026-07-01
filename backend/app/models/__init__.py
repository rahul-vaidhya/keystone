"""All SQLAlchemy ORM models, re-exported from one place.

Every model module must be imported somewhere on the path to ``Base.metadata`` or its
tables silently vanish from Alembic autogenerate. Import ORDER does not matter here:
every FK in this codebase is a string table-name reference (``ForeignKey("organizations.id")``),
never a direct class reference or ``relationship()``, so there is no load-time dependency
between model modules for SQLAlchemy to resolve. (ruff's isort keeps these alphabetical —
that's fine, don't fight it.)
"""

from __future__ import annotations

from app.models.chat import Conversation, Message
from app.models.documents import Document, DocumentStatus, DocumentTag, Folder, Tag
from app.models.identity import Organization, User
from app.models.ingestion import Chunk, Embedding, Section
from app.models.knowledge import Notebook, NotebookDocument
from app.platform.db import Base

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
]
