"""Retrieval Pydantic schemas. ``ContextBlock``/``RetrievalSearchResponse`` are the SHAPE
F40 (chat) will consume later — numbered context with source refs for citations
(architecture.md ``retrieve()``: "assemble_context(hits) -> numbered, with source refs").
Citation mapping itself is F41; this is the shape only.

Note: retrieval owns no table, so this file contains only Pydantic schemas (no ORM models).
"""

from __future__ import annotations

import uuid
from typing import Literal

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
    # From-scratch sparse IR channel — surfaced from ``ChunkHit.sparse_score`` /
    # ``ChunkHit.sparse_explanation`` unchanged, additive only (``None`` unless
    # ``SPARSE_RETRIEVAL_MODE != "off"`` and hybrid search sourced this block lexically).
    # Persisted into ``message_traces.hits`` with the rest of the block.
    sparse_score: float | None = None
    sparse_explanation: list[dict] | None = None


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


# ---- /retrieval/sparse-search: the from-scratch IR core, made visible ---------------------


class SparseZoneWeights(BaseModel):
    heading: float = Field(default=2.0, ge=0.0, le=10.0)
    body: float = Field(default=1.0, ge=0.0, le=10.0)


class SparseSearchRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["ranked", "boolean", "phrase"] = "ranked"
    scheme: Literal["tfidf", "bm25"] = "bm25"
    k: int = Field(default=10, ge=1, le=50)
    use_champions: bool = False
    idf_threshold: float = Field(default=0.0, ge=0.0, le=10.0)
    zone_weights: SparseZoneWeights = Field(default_factory=SparseZoneWeights)


class SparseTermStat(BaseModel):
    term: str
    surface: list[str]
    query_tf: int
    df: int
    idf: float
    postings: dict[str, int]
    champions: dict[str, int]
    eliminated: bool


class SparseRankedTrace(BaseModel):
    scheme: Literal["tfidf", "bm25"]
    zone_weights: dict[str, float]
    use_champions: bool
    idf_threshold: float
    active_terms: list[str]
    eliminated_terms: list[str]
    docs_with_postings: int
    champion_candidates: int | None
    docs_scored: int


class SparseBooleanOperand(BaseModel):
    word: str
    terms: list[str]
    negated: bool


class SparseBooleanStep(BaseModel):
    op: str
    term: str
    df: int
    result_size: int


class SparseBooleanClause(BaseModel):
    operands: list[SparseBooleanOperand]
    steps: list[SparseBooleanStep]
    result_size: int


class SparseBooleanTrace(BaseModel):
    operators: list[str]
    clauses: list[SparseBooleanClause]
    union_steps: list[SparseBooleanStep]


class SparsePhraseZone(BaseModel):
    zone: str
    postings: dict[str, int]
    candidates: int
    matched: int


class SparsePhraseTerm(BaseModel):
    term: str
    offset: int


class SparsePhraseTrace(BaseModel):
    phrase: str
    terms: list[SparsePhraseTerm]
    zones: list[SparsePhraseZone]
    candidates: int
    matched: int


class SparseQueryAnalysis(BaseModel):
    raw_tokens: list[str]
    casefolded: list[str]
    stop_words_removed: list[str]
    kept_tokens: list[str]
    stems: list[str]
    terms: list[SparseTermStat]
    ranked: SparseRankedTrace | None = None
    boolean: SparseBooleanTrace | None = None
    phrase: SparsePhraseTrace | None = None


class SparseIndexStats(BaseModel):
    n_docs: int
    vocabulary_size: int
    avg_postings_length: float
    zones: list[str]
    champion_r: int
    cached: bool
    build_ms: float
    query_ms: float
    total_ms: float


class SparseContribution(BaseModel):
    term: str
    zone: str
    tf: int
    idf: float
    weight: float


class SparsePhraseMatch(BaseModel):
    zone: str
    positions: list[int]


class SparseSearchResult(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    heading: str | None
    content: str
    snippet: str
    char_start: int
    char_end: int
    page_start: int | None = None
    page_end: int | None = None
    score: float | None = None
    contributions: list[SparseContribution] = []
    matched_terms: list[str] = []
    highlights: list[str] = []
    phrase_matches: list[SparsePhraseMatch] = []


class SparseSearchResponse(BaseModel):
    query: str
    mode: Literal["ranked", "boolean", "phrase"]
    analysis: SparseQueryAnalysis
    index_stats: SparseIndexStats
    total_matches: int
    results: list[SparseSearchResult]
