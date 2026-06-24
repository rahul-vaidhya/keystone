"""Chat Pydantic schemas. F40 built the request/answer shape; F41 adds ``ResolvedCitation``
(citations derived from the answer's ``[n]`` markers, not every retrieved block) and the
persisted ``conversation_id``/``message_id`` (F41's DoD: store in ``messages.citations``).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class ResolvedCitation(BaseModel):
    """One ``[n]`` marker from the model's answer, resolved to its source span — rebuilt
    from a fresh ``ingestion.service.get_chunks`` read of the chunk row (the
    source-of-truth table), not from the ``ContextBlock`` retrieval already had in hand.
    ``marker`` is the literal number the model cited (e.g. ``2`` for ``[2]``)."""

    marker: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    char_start: int
    char_end: int
    content: str


class ChatResponse(BaseModel):
    correlation_id: str
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    notebook_id: uuid.UUID
    query: str
    answer: str
    citations: list[ResolvedCitation]
    model: str
