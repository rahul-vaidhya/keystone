"""Notebooks domain errors."""

from __future__ import annotations


class KnowledgeError(Exception):
    """Base notebooks failure."""


class NotebookNotFound(KnowledgeError):
    pass


class NotebookAccessDenied(KnowledgeError):
    """Raised when an authenticated user (``ctx.user_id`` is set) who is neither the
    notebook's creator nor a share recipient (for view-level checks — manage-level
    checks require creator regardless of shares) requests it. Maps to 403, not 404 —
    unlike ``NotebookNotFound``, this deliberately confirms the notebook exists."""


class OverviewNotFound(KnowledgeError):
    """No Notebook Overview has been generated yet for this notebook. Maps to 404 —
    the notebook itself may well exist (and be fully visible to the caller); this is
    "the artifact hasn't been created yet", not "you can't see this notebook"."""


class OverviewUnavailable(KnowledgeError):
    """Raised by ``generate_overview`` when it must refuse to run: the feature is
    disabled (``settings.NOTEBOOK_OVERVIEW_ENABLED=False``), the notebook has zero
    documents carrying a V2 enrichment section summary (enrichment hasn't run yet), or
    the notebook's document count exceeds ``settings.BROAD_QUERY_MAX_DOCUMENTS`` (the
    same safety cap the P1 broad-query chat router uses). Unlike that router's silent,
    invisible fallback to the flat retrieval pipeline, this is a user-INITIATED action
    with no fallback artifact to silently substitute — so it surfaces as a clear 409
    the frontend can show as "can't generate an overview yet", never a silent no-op."""
