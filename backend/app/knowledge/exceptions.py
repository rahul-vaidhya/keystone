"""Knowledge (notebooks) domain errors."""

from __future__ import annotations


class KnowledgeError(Exception):
    """Base notebooks failure."""


class NotebookNotFound(KnowledgeError):
    pass
