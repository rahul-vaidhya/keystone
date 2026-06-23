"""Documents/folders/tags use cases."""

from __future__ import annotations

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


documents_service = DocumentsService()
