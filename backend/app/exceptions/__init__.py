"""All domain exceptions, re-exported from one place."""

from __future__ import annotations

from app.exceptions.auth import (
    AmbiguousLogin,
    AuthError,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    TargetUserNotFound,
    UserNotFound,
)
from app.exceptions.chat import GenerationFailed
from app.exceptions.documents import (
    DocumentNotFound,
    DocumentsError,
    FolderCycleError,
    FolderNameConflict,
    FolderNotEmpty,
    FolderNotFound,
    TagNotFound,
)
from app.exceptions.knowledge import KnowledgeError, NotebookNotFound

__all__ = [
    "AmbiguousLogin",
    "AuthError",
    "EmailTaken",
    "Forbidden",
    "InvalidCredentials",
    "TargetUserNotFound",
    "UserNotFound",
    "DocumentNotFound",
    "DocumentsError",
    "FolderCycleError",
    "FolderNameConflict",
    "FolderNotEmpty",
    "FolderNotFound",
    "TagNotFound",
    "KnowledgeError",
    "NotebookNotFound",
    "GenerationFailed",
]
