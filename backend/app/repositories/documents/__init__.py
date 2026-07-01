"""Documents repositories — all SQL for folders/tags/documents/document_tags lives here
(codestandards "Layering").

This package is a structural split of what used to be one flat ``repository.py`` (4
independent classes for 4 independent tables) — the refactor that introduced this split
made ZERO logic changes. Re-exported here so ``from app.repositories.documents import X``
keeps resolving unchanged for the sole caller, ``app.services.documents``.
"""

from __future__ import annotations

from app.repositories.documents.documents import DocumentRepository
from app.repositories.documents.folders import FolderRepository
from app.repositories.documents.tags import DocumentTagRepository, TagRepository

__all__ = [
    "DocumentRepository",
    "DocumentTagRepository",
    "FolderRepository",
    "TagRepository",
]
