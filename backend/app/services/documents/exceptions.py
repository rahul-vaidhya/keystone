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


class FolderAccessDenied(DocumentsError):
    """A member whose Access Roles don't grant them one of the folder's effective
    (inherited) access-controlling tags tried to rename/move/delete it, create a
    subfolder under it, or move a document into/out of it. Org owner/admin always
    bypass this (unlike notebook privacy) — they keep full document access. Maps to
    403, not the generic 400 ``DocumentsError`` gets."""


class TagNotFound(DocumentsError):
    pass


class DocumentNotFound(DocumentsError):
    pass


class UnsupportedFileType(DocumentsError):
    """Upload rejected before storage: only PDFs can be ingested (the real parser seam
    is PDF-only). Maps to 415 with a user-readable message."""
