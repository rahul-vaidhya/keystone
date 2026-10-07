"""Ingestion domain models and Pydantic schemas: sections, chunks, and embeddings
(architecture.md "sections tree — the key future-proofing" / "embeddings — polymorphic,
multi-granularity index"). ORM models are owned by ``ingestion`` because its stages
(structuring, embedding) are what produce them. ``retrieval`` (F31) reads this data only
through ``IngestionService.search_chunks`` — it does NOT import these ORM classes directly;
the join SQL lives in ``ingestion/repository.py`` per the module-boundary rule (a module's
tables stay behind its own service/repository, even for read-only cross-module access).
Structural fields only — ``summary``/``topics`` on ``Section`` are [later] V2 enrichment
columns, populated by a backfill job behind a flag, never by F21.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel
from sqlalchemy import Computed, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base
from app.services.seams.protocols import EMBED_DIM


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # sibling order
    depth: Mapped[int] = mapped_column(Integer, nullable=False)  # heading level / tree depth
    path: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. '1.2.3', fast subtree queries
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # [later] V2 enrichment
    topics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # [later] V2 enrichment
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # document-wide order
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Hybrid search's lexical candidate path (migration 0021): a STORED generated column
    # (`GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`), computed by
    # Postgres itself on every insert/update — the app NEVER writes to this column. The
    # `Computed(...)` marker here is DML-only signaling (it tells SQLAlchemy's ORM to
    # ALWAYS exclude this column from INSERT/UPDATE statements, including its
    # `insertmanyvalues` batch-insert path used by `bulk_create`, which otherwise sends
    # an explicit NULL for every row and Postgres rejects any explicit value — even
    # NULL — into a generated column); the actual DDL is owned entirely by migration
    # 0021's raw SQL, never regenerated from this model (this project never runs
    # Alembic autogenerate against `Base.metadata`, so no DDL drift risk). Backed by a
    # GIN index (`ix_chunks_content_tsv`) for `@@`/`ts_rank` queries.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Embedding(Base):
    """Polymorphic, multi-granularity vector index (architecture.md "embeddings"). F22
    inserts only ``owner_type='chunk'`` rows (``owner_id`` = ``chunks.id``); V2 enrichment
    later inserts ``'section'``/``'document'`` rows into this SAME table — no migration.
    ``model``/``dim`` are provenance, not display fields: retrieval filters
    ``model = :active_model`` so a re-embed under a new model name never returns duplicate
    hits per chunk. ``unique(owner_type, owner_id, model)`` is the idempotent-re-embed
    constraint — F22 upserts on it rather than delete-then-rebuild."""

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model", name="uq_embeddings_owner_model"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )  # denormalized: scope filter needs no join, dies with the document
    # [now] 'chunk' | [later] 'section'/'document'
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)  # which model produced this vector
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class SectionHit(BaseModel):
    """One coarse-pass hit over section embeddings (owner_type='section') — the shape
    ``EmbeddingRepository.search_sections`` produces from its embeddings/sections join
    (V2 hierarchical retrieval). ``distance`` is cosine distance (smaller = closer), per
    the ``vector_cosine_ops`` HNSW index from migration 0007."""

    section_id: uuid.UUID
    document_id: uuid.UUID
    heading: str | None
    path: str
    # V2 enrichment metadata — surfaced for observability logging only (never in the response).
    topics: list[str] | None = None
    distance: float

    model_config = {"from_attributes": True}


class ChunkHit(BaseModel):
    """One flat_vector search hit — the row shape ``EmbeddingRepository.search_chunks``
    produces from its embeddings/chunks join. ``distance`` is cosine distance (smaller =
    closer), per the ``vector_cosine_ops`` HNSW index from migration 0007."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    char_start: int
    char_end: int
    # `None` only for a lexical-only hit (hybrid search's full-text candidate path,
    # migration 0021 — a lexical match has no cosine distance). Every vector-kNN hit
    # (flat or hierarchical) still always carries a real float, unchanged.
    distance: float | None = None
    # [later] V2 reranker — stamped by `Reranker.rerank` (architecture.md "A Reranker
    # seam is added in V2, not now"), additive only. Always `None` when
    # `RERANKER_ENABLED=False` (the default); never removes/replaces `distance`, which
    # stays the raw cosine-distance provenance from the kNN search regardless.
    rerank_score: float | None = None
    # From-scratch sparse IR lexical channel (``SPARSE_RETRIEVAL_MODE != "off"``,
    # app.services.retrieval.sparse_channel) — additive only, always ``None`` on every
    # other path. ``sparse_score`` is the in-house tf-idf/BM25 score;
    # ``sparse_explanation`` is the per-(term, zone) breakdown that sums to it:
    # ``[{"term", "zone", "tf", "idf", "weight"}]``.
    sparse_score: float | None = None
    sparse_explanation: list[dict] | None = None
    # Hybrid search only (set by ``fuse_rrf``, ``None`` otherwise): the RRF score that
    # decides the fused order, and this chunk's 1-indexed rank in each candidate list
    # (``None`` = not in that list's top-K).
    fused_score: float | None = None
    vector_rank: int | None = None
    lexical_rank: int | None = None

    model_config = {"from_attributes": True}


class SparseIndexChunk(BaseModel):
    """One chunk plus its owning section's heading — the input unit of the in-house
    sparse index (``app.services.retrieval.sparse_channel``), produced by
    ``ingestion_service.list_chunks_for_sparse_index``. ``heading`` is ``None`` when the
    chunk has no section or the section has no heading."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    section_id: uuid.UUID | None
    content: str
    char_start: int
    char_end: int
    heading: str | None = None


class ChunkRecord(BaseModel):
    """A chunk row fetched directly by id (no kNN, no distance) — the shape
    ``ingestion.service.get_chunks`` returns for citation resolution (F41): the
    source-of-truth row a caller re-confirms a citation's span against, rather than
    trusting a copy made earlier in the request (e.g. ``retrieval``'s ``ContextBlock``).
    ``page_start``/``page_end`` are the owning section's page range (outer-joined —
    ``section_id`` is nullable and a section could theoretically be missing), never
    fabricated: both stay ``None`` when there is no section or the section has no page
    info recovered for it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    char_start: int
    char_end: int
    page_start: int | None = None
    page_end: int | None = None

    model_config = {"from_attributes": True}


class EnrichmentBackfillResult(BaseModel):
    """Summary of an org-wide enrichment backfill run (POST /ingestion/enrich-backfill)."""

    enriched: int
    skipped: int
    failed: int
