"""Documents/folders/tags use cases."""

from __future__ import annotations

import hashlib
import uuid

from app.documents.exceptions import DocumentNotFound, FolderNotFound, TagNotFound
from app.documents.repository import (
    DocumentRepository,
    DocumentTagRepository,
    FolderRepository,
    TagRepository,
)
from app.documents.schemas import DocumentOut, FolderCreate, FolderOut, TagCreate, TagOut
from app.platform import db as db_mod
from app.platform.context import TenantContext
from app.platform.storage import ObjectStore, build_storage_key


class DocumentsService:
    async def create_folder(self, ctx: TenantContext, req: FolderCreate) -> FolderOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = FolderRepository(session, ctx)
            path = req.name
            if req.parent_id is not None:
                parent = await repo.get_by_id(req.parent_id)
                if parent is None:
                    raise FolderNotFound("Parent folder not found")
                path = f"{parent.path}/{req.name}"
            folder = await repo.create(parent_id=req.parent_id, name=req.name, path=path)
        return FolderOut.model_validate(folder)

    async def list_folders(self, ctx: TenantContext) -> list[FolderOut]:
        async with db_mod.sessionmaker() as session:
            folders = await FolderRepository(session, ctx).list()
        return [FolderOut.model_validate(f) for f in folders]

    async def get_folder(self, ctx: TenantContext, folder_id: uuid.UUID) -> FolderOut:
        async with db_mod.sessionmaker() as session:
            folder = await FolderRepository(session, ctx).get_by_id(folder_id)
        if folder is None:
            raise FolderNotFound("Folder not found")
        return FolderOut.model_validate(folder)

    async def delete_folder(self, ctx: TenantContext, folder_id: uuid.UUID) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = FolderRepository(session, ctx)
            folder = await repo.get_by_id(folder_id)
            if folder is None:
                raise FolderNotFound("Folder not found")
            await repo.delete(folder)

    async def create_tag(self, ctx: TenantContext, req: TagCreate) -> TagOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = TagRepository(session, ctx)
            tag = await repo.get_by_name(req.name) or await repo.create(name=req.name)
        return TagOut.model_validate(tag)

    async def list_tags(self, ctx: TenantContext) -> list[TagOut]:
        async with db_mod.sessionmaker() as session:
            tags = await TagRepository(session, ctx).list()
        return [TagOut.model_validate(t) for t in tags]

    async def delete_tag(self, ctx: TenantContext, tag_id: uuid.UUID) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = TagRepository(session, ctx)
            tag = await repo.get_by_id(tag_id)
            if tag is None:
                raise TagNotFound("Tag not found")
            await repo.delete(tag)

    async def tag_document(
        self, ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            if await DocumentRepository(session, ctx).get_by_id(document_id) is None:
                raise DocumentNotFound("Document not found")
            if await TagRepository(session, ctx).get_by_id(tag_id) is None:
                raise TagNotFound("Tag not found")
            await DocumentTagRepository(session, ctx).attach(document_id, tag_id)

    async def untag_document(
        self, ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            await DocumentTagRepository(session, ctx).detach(document_id, tag_id)

    async def list_documents(
        self,
        ctx: TenantContext,
        *,
        folder_id: uuid.UUID | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[DocumentOut]:
        async with db_mod.sessionmaker() as session:
            docs = await DocumentRepository(session, ctx).list(folder_id=folder_id, tag_id=tag_id)
        return [DocumentOut.model_validate(d) for d in docs]

    async def upload_document(
        self,
        ctx: TenantContext,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        folder_id: uuid.UUID | None,
        object_store: ObjectStore,
    ) -> tuple[DocumentOut, bool]:
        """Upload to the object store and record the document. Re-uploading a byte-identical
        file (same org, same checksum) returns the existing document instead of a duplicate
        (``unique(org_id, checksum)`` — the DoD this enforces)."""
        checksum = hashlib.sha256(data).hexdigest()
        async with db_mod.sessionmaker() as session, session.begin():
            repo = DocumentRepository(session, ctx)
            if folder_id is not None:
                if await FolderRepository(session, ctx).get_by_id(folder_id) is None:
                    raise FolderNotFound("Folder not found")

            existing = await repo.get_by_checksum(checksum)
            if existing is not None:
                return DocumentOut.model_validate(existing), False

            document = await repo.create_upload(folder_id=folder_id, title=filename)
            key = build_storage_key(ctx.org_id, document.id, filename)
            await object_store.put(key, data, content_type)
            await repo.mark_uploaded(
                document,
                storage_key=key,
                checksum=checksum,
                mime_type=content_type,
                byte_size=len(data),
            )
            out = DocumentOut.model_validate(document)
        return out, True

    async def begin_parsing(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        """Called by ``ingestion.service`` (never the repository directly — module boundary
        rule) to claim a document for the parsing stage. Returns the document unchanged, with
        whatever status it already had, when parsing isn't eligible to (re)start — the caller
        checks the returned status to decide whether to proceed."""
        async with db_mod.sessionmaker() as session, session.begin():
            document = await DocumentRepository(session, ctx).begin_parsing(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            out = DocumentOut.model_validate(document)
        return out

    async def complete_parsing(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        language: str,
        page_count: int,
        artifact_key: str,
    ) -> DocumentOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = DocumentRepository(session, ctx)
            document = await repo.get_by_id(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            await repo.complete_parsing(
                document, language=language, page_count=page_count, artifact_key=artifact_key
            )
            out = DocumentOut.model_validate(document)
        return out

    async def fail_stage(
        self, ctx: TenantContext, document_id: uuid.UUID, *, failed_stage: str, error_detail: str
    ) -> DocumentOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = DocumentRepository(session, ctx)
            document = await repo.get_by_id(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            await repo.mark_failed(document, failed_stage=failed_stage, error_detail=error_detail)
            out = DocumentOut.model_validate(document)
        return out

    async def begin_structuring(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        """Called by ``ingestion.service`` (never the repository directly — module boundary
        rule) to claim a document for the structuring stage. Returns the document unchanged
        when structuring isn't eligible to (re)start — the caller checks the returned status."""
        async with db_mod.sessionmaker() as session, session.begin():
            document = await DocumentRepository(session, ctx).begin_structuring(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            out = DocumentOut.model_validate(document)
        return out

    async def complete_structuring(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = DocumentRepository(session, ctx)
            document = await repo.get_by_id(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            await repo.complete_structuring(document)
            out = DocumentOut.model_validate(document)
        return out

    async def begin_embedding(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        """Called by ``ingestion.service`` (never the repository directly — module boundary
        rule) to claim a document for the embedding stage. Returns the document unchanged
        when embedding isn't eligible to (re)start — the caller checks the returned status."""
        async with db_mod.sessionmaker() as session, session.begin():
            document = await DocumentRepository(session, ctx).begin_embedding(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            out = DocumentOut.model_validate(document)
        return out

    async def complete_embedding(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = DocumentRepository(session, ctx)
            document = await repo.get_by_id(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
            await repo.complete_embedding(document)
            out = DocumentOut.model_validate(document)
        return out

    async def get_document(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        """Org-scoped existence lookup for OTHER modules (module-boundary rule: cross-module
        calls go through a service, never a repository). First caller: ``knowledge.service``
        validates a document belongs to the attaching org before joining it into a notebook —
        a plain FK can't express that, since it only proves the document exists somewhere, not
        that it's in the same org as the notebook."""
        async with db_mod.sessionmaker() as session:
            document = await DocumentRepository(session, ctx).get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        return DocumentOut.model_validate(document)

    async def list_by_ids(
        self, ctx: TenantContext, document_ids: list[uuid.UUID]
    ) -> list[DocumentOut]:
        """Org-scoped batch lookup for OTHER modules (module-boundary rule). First caller:
        ``knowledge.service`` resolving the document rows attached to a notebook — knowledge
        owns the join table, not ``documents`` itself, so it asks this service rather than
        reading the ``documents`` table directly."""
        async with db_mod.sessionmaker() as session:
            documents = await DocumentRepository(session, ctx).list_by_ids(document_ids)
        return [DocumentOut.model_validate(d) for d in documents]

    async def get_parse_artifact_key(self, ctx: TenantContext, document_id: uuid.UUID) -> str:
        """Narrow accessor for ``ingestion.service`` (structuring stage): the F20 parsing
        artifact key, written into ``metadata_`` by ``complete_parsing``. A dedicated method
        rather than exposing ``metadata`` on ``DocumentOut`` — keeps the internal storage
        layout out of the public HTTP response shape."""
        async with db_mod.sessionmaker() as session:
            document = await DocumentRepository(session, ctx).get_by_id(document_id)
            if document is None:
                raise DocumentNotFound("Document not found")
        return document.metadata_["parse_artifact_key"]


documents_service = DocumentsService()
