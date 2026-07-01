"""Ingestion-owned value types exposed to other modules through ``IngestionService``
(module-boundary rule: callers get this shape via the service, never by importing
``ingestion.models``/``ingestion.repository`` directly).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ChunkHit(BaseModel):
    """One flat_vector search hit — the row shape ``EmbeddingRepository.search_chunks``
    produces from its embeddings/chunks join. ``distance`` is cosine distance (smaller =
    closer), per the ``vector_cosine_ops`` HNSW index from migration 0007."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    char_start: int
    char_end: int
    distance: float

    model_config = {"from_attributes": True}


class ChunkRecord(BaseModel):
    """A chunk row fetched directly by id (no kNN, no distance) — the shape
    ``ingestion.service.get_chunks`` returns for citation resolution (F41): the
    source-of-truth row a caller re-confirms a citation's span against, rather than
    trusting a copy made earlier in the request (e.g. ``retrieval``'s ``ContextBlock``)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    char_start: int
    char_end: int

    model_config = {"from_attributes": True}
