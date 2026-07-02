"""Documents domain errors."""

from __future__ import annotations


class DocumentsError(Exception):
    """Base documents/folders/tags failure."""


class FolderNotFound(DocumentsError):
    pass


class FolderNotEmpty(DocumentsError):
    """Block-mode delete on a folder that still has child folders or documents."""


class FolderNameConflict(DocumentsError):
    """Rename/move would collide with an existing folder under the same target parent."""


class FolderCycleError(DocumentsError):
    """Move would place a folder inside itself or one of its own descendants."""


class TagNotFound(DocumentsError):
    pass


class DocumentNotFound(DocumentsError):
    pass
