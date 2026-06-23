"""Documents domain errors."""

from __future__ import annotations


class DocumentsError(Exception):
    """Base documents/folders/tags failure."""


class FolderNotFound(DocumentsError):
    pass


class TagNotFound(DocumentsError):
    pass


class DocumentNotFound(DocumentsError):
    pass
