"""Ingestion domain models: sections, chunks, and embeddings (architecture.md "sections
tree — the key future-proofing" / "embeddings — polymorphic, multi-granularity index").
Owned by ``ingestion`` because its stages (structuring, embedding) are what produce them.
``retrieval`` (F31) reads this data only through ``IngestionService.search_chunks`` — it
does NOT import these ORM classes directly; the join SQL lives in
``ingestion/repository.py`` per the module-boundary rule (a module's tables stay behind its
own service/repository, even for read-only cross-module access). Structural fields only —
``summary``/``topics`` on ``Section`` are [later] V2 enrichment columns, populated by a
backfill job behind a flag, never by F21.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base
from app.platform.seams import EMBED_DIM


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
