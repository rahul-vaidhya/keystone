"""Retrieval use cases — the MVP ``flat_vector`` path (architecture.md "Retrieval
pipeline"). Owns no table; reaches other modules only through their ``service``
(module-boundary rule).

This package is a structural split of what used to be one flat ``retrieval.py`` (324
lines mixing permission resolution, pure result-transform functions, and search-strategy
orchestration) — the refactor that introduced this split made ZERO logic changes.
``permissions.py`` holds ``resolve_allowed_documents`` (a genuinely independent data
flow: documents/folders/tags, never chunks/embeddings/seams); ``fusion.py`` holds the
pure functions ``assemble_context``/``fuse_rrf``; ``service.py`` holds
``RetrievalService`` (search-strategy orchestration: flat/hierarchical/hybrid/rerank
composition). Same convention as ``app.services.ingestion``/``app.services.chat``. Every
name a real call site or test imports today resolves at the exact same path afterward.
"""

from __future__ import annotations

from app.services.retrieval.fusion import assemble_context as assemble_context
from app.services.retrieval.fusion import fuse_rrf as fuse_rrf
from app.services.retrieval.permissions import (
    resolve_allowed_documents as resolve_allowed_documents,
)
from app.services.retrieval.service import RetrievalService as RetrievalService
from app.services.retrieval.service import retrieval_service as retrieval_service
from app.services.retrieval.sparse.trace import (
    MalformedBooleanQuery as MalformedBooleanQuery,
)
