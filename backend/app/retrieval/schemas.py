"""Retrieval Pydantic schemas. ``ContextBlock``/``RetrievalSearchResponse`` are the SHAPE
F40 (chat) will consume later — numbered context with source refs for citations
(architecture.md ``retrieve()``: "assemble_context(hits) -> numbered, with source refs").
Citation mapping itself is F41; this is the shape only.
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


class RetrievalSearchResponse(BaseModel):
    query: str
    results: list[ContextBlock]
