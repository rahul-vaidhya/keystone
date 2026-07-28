"""Retrieval Pydantic schemas. ``ContextBlock``/``RetrievalSearchResponse`` are the SHAPE
F40 (chat) will consume later — numbered context with source refs for citations
(architecture.md ``retrieve()``: "assemble_context(hits) -> numbered, with source refs").
Citation mapping itself is F41; this is the shape only.

Note: retrieval owns no table, so this file contains only Pydantic schemas (no ORM models).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class RetrievalSearchRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class ContextBlock(BaseModel):
    index: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    char_start: int
    char_end: int
    content: str
    # `None` only for a lexical-only hit (hybrid search's full-text candidate path,
    # migration 0021 — a lexical match has no cosine distance). Every vector-kNN block
    # (flat or hierarchical) still always carries a real float, unchanged — surfaced
    # from `ChunkHit.distance` unchanged.
    distance: float | None = None
    # [later] V2 reranker — surfaced from `ChunkHit.rerank_score` unchanged, additive
    # only. Always `None` when `RERANKER_ENABLED=False` (the default); `distance` is
    # never touched or removed.
    rerank_score: float | None = None


class RetrievalSearchResponse(BaseModel):
    query: str
    results: list[ContextBlock]
