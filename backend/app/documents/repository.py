"""Documents repositories — all SQL for folders/tags/documents/document_tags lives here
(codestandards "Layering")."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.documents.models import Document, DocumentTag, Folder, Tag
from app.documents.status import DocumentStatus
from app.platform.repository import BaseRepository


class FolderRepository(BaseRepository[Folder]):
    model = Folder

    async def list(self) -> list[Folder]:
        stmt = self._scoped().order_by(Folder.path)
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, folder_id: uuid.UUID) -> Folder | None:
        stmt = self._scoped().where(Folder.id == folder_id)
        return await self._db.scalar(stmt)

    async def create(self, *, parent_id: uuid.UUID | None, name: str, path: str) -> Folder:
        folder = Folder(org_id=self._ctx.org_id, parent_id=parent_id, name=name, path=path)
        self._db.add(folder)
        await self._db.flush()
        return folder

    async def delete(self, folder: Folder) -> None:
        await self._db.delete(folder)


class TagRepository(BaseRepository[Tag]):
    model = Tag

    async def list(self) -> list[Tag]:
        stmt = self._scoped().order_by(Tag.name)
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, tag_id: uuid.UUID) -> Tag | None:
        stmt = self._scoped().where(Tag.id == tag_id)
        return await self._db.scalar(stmt)

    async def get_by_name(self, name: str) -> Tag | None:
        stmt = self._scoped().where(Tag.name == name)
        return await self._db.scalar(stmt)

    async def create(self, *, name: str) -> Tag:
        tag = Tag(org_id=self._ctx.org_id, name=name)
        self._db.add(tag)
        await self._db.flush()
        return tag

    async def delete(self, tag: Tag) -> None:
        await self._db.delete(tag)


class DocumentRepository(BaseRepository[Document]):
    model = Document

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        stmt = self._scoped().where(Document.id == document_id)
        return await self._db.scalar(stmt)

    async def get_by_checksum(self, checksum: str) -> Document | None:
        stmt = self._scoped().where(Document.checksum == checksum)
        return await self._db.scalar(stmt)

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


class DocumentTagRepository(BaseRepository[DocumentTag]):
    model = DocumentTag

    async def attach(self, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        """Idempotent: attaching an already-attached tag is a no-op."""
        stmt = (
            pg_insert(DocumentTag)
            .values(org_id=self._ctx.org_id, document_id=document_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["document_id", "tag_id"])
        )
        await self._db.execute(stmt)

    async def detach(self, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        """Idempotent: detaching a tag that isn't attached is a no-op."""
        stmt = self._scoped().where(
            DocumentTag.document_id == document_id, DocumentTag.tag_id == tag_id
        )
        link = await self._db.scalar(stmt)
        if link is not None:
            await self._db.delete(link)
