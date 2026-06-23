"""Documents repositories — all SQL for folders/tags/documents/document_tags lives here
(codestandards "Layering").

This package is a structural split of what used to be one flat ``repository.py`` (4
independent classes for 4 independent tables) — the refactor that introduced this split
made ZERO logic changes. Re-exported here so ``from app.documents.repository import X``
keeps resolving unchanged for the sole caller, ``documents.service``.
"""

from __future__ import annotations

from app.documents.repository.documents import DocumentRepository
from app.documents.repository.folders import FolderRepository
from app.documents.repository.tags import DocumentTagRepository, TagRepository

__all__ = [
    "DocumentRepository",
    "DocumentTagRepository",
    "FolderRepository",
    "TagRepository",
]
