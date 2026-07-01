"""Documents domain models: folders, tags, document_tags, and documents.

F11 built folders + tags, with ``Document`` landing as a minimal anchor
(id/org_id/folder_id/title) so ``document_tags`` had something to FK to. F12 (upload +
dedupe) ALTERs that same table here to add the storage/checksum/pipeline-status columns —
no new table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class DocumentStatus(StrEnum):
    """Authoritative ingestion pipeline status enum (architecture.md "Ingestion
    pipeline"). Single source of truth, referenced everywhere a document's pipeline
    state is read or written. There is no ``parsed`` state — do not invent ad-hoc
    values."""

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    STRUCTURING = "STRUCTURING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        UniqueConstraint("org_id", "parent_id", "name", name="uq_folders_org_parent_name"),
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
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # materialized path, e.g. 'HR/Policies'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_tags_org_name"),)

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Document(Base):
    """F12 ALTERed this table (originally the F11 anchor) to add upload/dedupe/pipeline
    columns: ``storage_key``/``checksum`` are set at upload time; ``page_count``/
    ``language`` are set later by parsing (F20); ``status`` drives the ingestion pipeline
    (``documents/status.py``)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("org_id", "checksum", name="uq_documents_org_checksum"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(Text, nullable=True)  # dedupe key
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # [later] parse (F20)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)  # [later] parse (F20)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=DocumentStatus.UPLOADED.value, server_default="UPLOADED"
    )
    failed_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentTag(Base):
    __tablename__ = "document_tags"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
