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


class SectionSummaryHit(BaseModel):
    """One section's V2 enrichment summary (``sections.summary``/``sections.topics``) —
    the broad-query map-reduce strategy's (``app.services.retrieval.mapreduce``) map-step
    input unit, produced by ``ingestion_service.list_section_summaries``. Only sections
    carrying a non-null summary are ever represented here — a section enrichment hasn't
    reached yet is silently excluded upstream, never represented as an empty-summary hit
    (same "never fabricate" discipline as every other hit shape in this codebase)."""

    section_id: uuid.UUID
    document_id: uuid.UUID
    heading: str | None
    summary: str
    # V2 enrichment metadata, carried through for parity with `SectionHit.topics`
    # (app.models.ingestion) — not currently read by the map/reduce prompts themselves.
    topics: list[str] | None = None

    model_config = {"from_attributes": True}


class SynthesisBlock(BaseModel):
    """One fragment of a broad-query synthesized answer — the reduce step's output unit
    (``app.services.retrieval.mapreduce``), mirroring how ``ContextBlock`` carries
    chunk-level provenance for the flat path, but at SECTION granularity. ``index``
    numbers it the same way ``ContextBlock.index`` does, so the reduce LLM's own ``[n]``
    citation markers resolve back to these blocks 1:1. ``section_ids``/``headings``/
    ``document_ids`` are lists (not singular fields) so a future batched-map-step variant
    (several sections synthesized into one block) has somewhere to carry multiple
    provenance entries; the current per-section map step always populates exactly one
    entry in each list per block."""

    index: int
    content: str
    section_ids: list[uuid.UUID]
    headings: list[str | None]
    document_ids: list[uuid.UUID]
