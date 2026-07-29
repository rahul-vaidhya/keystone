"""Knowledge (notebooks) domain models and Pydantic schemas.

Public terminology is "Notebook" everywhere (schemas/services/routes/tests); the
underlying tables stay ``knowledge_bases``/``knowledge_base_documents`` to match the
names already locked in architecture.md's data model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base
from app.models.chat import ResolvedCitation


class Notebook(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NotebookDocument(Base):
    __tablename__ = "knowledge_base_documents"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotebookShare(Base):
    """A direct per-person grant of view+chat access to a private notebook — see
    migration 0020's docstring for why this is per-person rather than routed through
    the Access Role group system."""

    __tablename__ = "notebook_shares"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    shared_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotebookOverview(Base):
    """On-demand, cached "gist of everything" artifact for a notebook (P1 roadmap,
    Notebook Overview feature) — one row per notebook (``notebook_id`` UNIQUE),
    regenerated in place via upsert, never accumulating history. Built by
    ``app.services.knowledge.overview`` reusing ``app.services.retrieval.mapreduce``'s
    map-reduce pipeline over V2 enrichment section summaries — the SAME mechanism
    ``app.services.chat.broad_query`` uses for a live chat answer, but persisted
    standalone rather than answered per-message. ``stale`` is set (never cleared except
    by a fresh regenerate) whenever the notebook's document set changes (attach/detach)
    — the cached content itself is never deleted or auto-regenerated, only flagged so
    the frontend can show a "this may be out of date" banner."""

    __tablename__ = "notebook_overviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # A jsonb list of ResolvedCitation(citation_type="section")-shaped dicts — same
    # "store the dict, validate into the Pydantic model on read" precedent as
    # ``Message.citations``.
    citations: Mapped[list] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


# ---- API schemas ----


class NotebookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class NotebookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class NotebookOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotebookShareCreate(BaseModel):
    user_id: uuid.UUID


class NotebookShareOut(BaseModel):
    user_id: uuid.UUID
    email: str
    created_at: datetime


class NotebookOverviewOut(BaseModel):
    """The persisted overview, verbatim. ``citations`` round-trips straight from the
    jsonb column into ``ResolvedCitation(citation_type="section")`` instances — written
    in exactly that shape by ``app.services.knowledge.overview.generate_overview``."""

    id: uuid.UUID
    notebook_id: uuid.UUID
    content: str
    citations: list[ResolvedCitation]
    generated_at: datetime
    generated_by: uuid.UUID | None
    source_document_count: int
    stale: bool

    model_config = {"from_attributes": True}
