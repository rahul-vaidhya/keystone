"""``documents`` table — all SQL for documents, upload/dedupe, and the F20-F22 ingestion
status-pipeline transitions (codestandards "Layering")."""

from __future__ import annotations

import uuid

from app.documents.models import Document, DocumentTag
from app.documents.status import DocumentStatus
from app.platform.repository import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        stmt = self._scoped().where(Document.id == document_id)
        return await self._db.scalar(stmt)

    async def get_by_checksum(self, checksum: str) -> Document | None:
        stmt = self._scoped().where(Document.checksum == checksum)
        return await self._db.scalar(stmt)

    async def list_by_ids(self, document_ids: list[uuid.UUID]) -> list[Document]:
        """Org-scoped batch lookup. First caller: ``knowledge.service`` resolving the document
        rows for a notebook's attached document ids (knowledge owns the join table, not
        ``documents`` itself, so it asks this service rather than reading the table directly)."""
        if not document_ids:
            return []
        stmt = self._scoped().where(Document.id.in_(document_ids))
        return list(await self._db.scalars(stmt))

    async def list(
        self, *, folder_id: uuid.UUID | None = None, tag_id: uuid.UUID | None = None
    ) -> list[Document]:
        stmt = self._scoped()
        if folder_id is not None:
            stmt = stmt.where(Document.folder_id == folder_id)
        if tag_id is not None:
            stmt = stmt.join(DocumentTag, DocumentTag.document_id == Document.id).where(
                DocumentTag.tag_id == tag_id
            )
        stmt = stmt.order_by(Document.created_at)
        return list(await self._db.scalars(stmt))

    async def create_upload(self, *, folder_id: uuid.UUID | None, title: str) -> Document:
        """Insert the row with an id assigned (flushed) but no storage_key/checksum yet —
        the caller needs the id to build the object-store key before the upload completes."""
        document = Document(org_id=self._ctx.org_id, folder_id=folder_id, title=title)
        self._db.add(document)
        await self._db.flush()
        return document

    async def mark_uploaded(
        self,
        document: Document,
        *,
        storage_key: str,
        checksum: str,
        mime_type: str,
        byte_size: int,
    ) -> None:
        document.storage_key = storage_key
        document.checksum = checksum
        document.mime_type = mime_type
        document.byte_size = byte_size
        await self._db.flush()

    async def begin_parsing(self, document_id: uuid.UUID) -> Document | None:
        """Transition to PARSING if eligible; otherwise return the document unchanged so the
        caller can treat it as an idempotent no-op (F20: "parsing must be idempotent and
        resumable"). Eligible: a fresh ``UPLOADED`` doc, a doc stuck in ``PARSING`` (crashed
        before the artifact was persisted, so there's nothing to resume from but a re-parse),
        or a doc that previously ``FAILED`` at the parsing stage (retry). A doc already past
        parsing (``STRUCTURING``/``EMBEDDING``/``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status in (DocumentStatus.UPLOADED, DocumentStatus.PARSING) or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.PARSING
        )
        if eligible:
            document.status = DocumentStatus.PARSING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_parsing(
        self, document: Document, *, language: str, page_count: int, artifact_key: str
    ) -> None:
        document.language = language
        document.page_count = page_count
        document.metadata_ = {**document.metadata_, "parse_artifact_key": artifact_key}
        document.status = DocumentStatus.STRUCTURING
        await self._db.flush()

    async def mark_failed(
        self, document: Document, *, failed_stage: str, error_detail: str
    ) -> None:
        document.status = DocumentStatus.FAILED
        document.failed_stage = failed_stage
        document.error_detail = error_detail
        await self._db.flush()

    async def begin_structuring(self, document_id: uuid.UUID) -> Document | None:
        """Transition to (or remain in) ``STRUCTURING`` if eligible; otherwise return the
        document unchanged so the caller treats it as an idempotent no-op (F21: "idempotent
        re-run"). Eligible: a doc already in ``STRUCTURING`` (the state F20 leaves it in, or
        a crashed-and-resumed structuring run — there's nothing to resume but a rebuild), or
        a doc that previously ``FAILED`` at the structuring stage (retry). A doc already past
        structuring (``EMBEDDING``/``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status == DocumentStatus.STRUCTURING or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.STRUCTURING
        )
        if eligible:
            document.status = DocumentStatus.STRUCTURING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_structuring(self, document: Document) -> None:
        document.status = DocumentStatus.EMBEDDING
        await self._db.flush()

    async def begin_embedding(self, document_id: uuid.UUID) -> Document | None:
        """Transition to (or remain in) ``EMBEDDING`` if eligible; otherwise return the
        document unchanged so the caller treats it as an idempotent no-op (F22: "re-running
        embedding must not create duplicate rows"). Eligible: a doc already in ``EMBEDDING``
        (the state F21 leaves it in, or a crashed-and-resumed embedding run), or a doc that
        previously ``FAILED`` at the embedding stage (retry). A doc already past embedding
        (``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status == DocumentStatus.EMBEDDING or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.EMBEDDING
        )
        if eligible:
            document.status = DocumentStatus.EMBEDDING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_embedding(self, document: Document) -> None:
        document.status = DocumentStatus.READY
        await self._db.flush()
