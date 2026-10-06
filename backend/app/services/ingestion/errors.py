"""User-facing failure messages for ingestion stages.

``documents.error_detail`` is shown verbatim in the repository UI, so it must never
carry raw exception text (vendor names, internal feature IDs, library errors like
pypdf's "Stream has ended unexpectedly"). Each stage logs the raw ``str(exc)`` at
WARNING before calling ``documents_service.fail_stage`` — this module only decides the
short, plain-language message that gets persisted.
"""

from __future__ import annotations

from app.models.documents import DocumentStatus
from app.services.seams.types import SeamNotConfigured, SeamTransientError

UNSUPPORTED_TYPE = "This file type isn't supported. Please upload a PDF."
PASSWORD_PROTECTED = "This PDF is password-protected. Remove the password and upload it again."
NO_TEXT = "No readable text was found in this PDF (it may be empty or a low-quality scan)."
UNREADABLE_PDF = "This file couldn't be read as a PDF (it may be corrupted or password-protected)."
SERVICE_UNAVAILABLE = (
    "The document processing service is temporarily unavailable. Please try again later."
)
STRUCTURING_FAILED = "We couldn't process this document's contents. Please try again later."
EMBEDDING_FAILED = "We couldn't index this document for search. Please try again later."


def _is_service_error(exc: BaseException) -> bool:
    return isinstance(exc, (SeamTransientError, SeamNotConfigured, TimeoutError))


def user_facing_error(stage: DocumentStatus, exc: BaseException) -> str:
    """Map a stage failure to a short, stable, user-readable message."""
    if _is_service_error(exc):
        return SERVICE_UNAVAILABLE

    if stage == DocumentStatus.PARSING:
        message = str(exc).lower()
        if "pdf only" in message or ("unsupported" in message and "mime" in message):
            return UNSUPPORTED_TYPE
        if "encrypted" in message or "password" in message:
            return PASSWORD_PROTECTED
        if "negligible text" in message:
            return NO_TEXT
        return UNREADABLE_PDF
    if stage == DocumentStatus.STRUCTURING:
        return STRUCTURING_FAILED
    if stage == DocumentStatus.EMBEDDING:
        return EMBEDDING_FAILED
    return STRUCTURING_FAILED
