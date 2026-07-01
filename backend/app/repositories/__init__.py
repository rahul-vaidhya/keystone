"""All repositories (data access), re-exported from one place."""

from __future__ import annotations

from app.repositories.auth import AuthRepository, OrganizationRepository, UserRepository
from app.repositories.chat import ConversationRepository, MessageRepository
from app.repositories.documents import (
    DocumentRepository,
    DocumentTagRepository,
    FolderRepository,
    TagRepository,
)
from app.repositories.ingestion import ChunkRepository, EmbeddingRepository, SectionRepository
from app.repositories.knowledge import NotebookDocumentRepository, NotebookRepository

__all__ = [
    "AuthRepository",
    "OrganizationRepository",
    "UserRepository",
    "DocumentRepository",
    "DocumentTagRepository",
    "FolderRepository",
    "TagRepository",
    "ChunkRepository",
    "EmbeddingRepository",
    "SectionRepository",
    "NotebookDocumentRepository",
    "NotebookRepository",
    "ConversationRepository",
    "MessageRepository",
]
