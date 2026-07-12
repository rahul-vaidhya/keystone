"""Tag + document-tag use cases."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import DocumentTag, FolderTag, Tag, TagCreate, TagOut
from app.services.base import BaseRepository
from app.services.documents.documents import DocumentRepository
from app.services.documents.exceptions import DocumentNotFound, FolderNotFound, TagNotFound


# ---- repository ----
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

    async def list_tag_ids_by_documents(
        self, document_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Batch form, used by ``retrieval.resolve_allowed_documents`` to compute each
        document's own direct tags without one query per document."""
        if not document_ids:
            return {}
        stmt = self._scoped().where(DocumentTag.document_id.in_(document_ids))
        result: dict[uuid.UUID, list[uuid.UUID]] = {did: [] for did in document_ids}
        for row in await self._db.scalars(stmt):
            result[row.document_id].append(row.tag_id)
        return result


class FolderTagRepository(BaseRepository[FolderTag]):
    """Mirrors ``DocumentTagRepository`` — folders can carry tags too (0012)."""

    model = FolderTag

    async def attach(self, folder_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        stmt = (
            pg_insert(FolderTag)
            .values(org_id=self._ctx.org_id, folder_id=folder_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["folder_id", "tag_id"])
        )
        await self._db.execute(stmt)

    async def detach(self, folder_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        stmt = self._scoped().where(FolderTag.folder_id == folder_id, FolderTag.tag_id == tag_id)
        link = await self._db.scalar(stmt)
        if link is not None:
            await self._db.delete(link)

    async def list_tag_ids(self, folder_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = self._scoped().where(FolderTag.folder_id == folder_id)
        return [row.tag_id for row in await self._db.scalars(stmt)]

    async def list_tag_ids_by_folders(
        self, folder_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Batch form of ``list_tag_ids`` for a whole folder list (used by
        ``list_folders`` so it doesn't issue one query per folder)."""
        if not folder_ids:
            return {}
        stmt = self._scoped().where(FolderTag.folder_id.in_(folder_ids))
        result: dict[uuid.UUID, list[uuid.UUID]] = {fid: [] for fid in folder_ids}
        for row in await self._db.scalars(stmt):
            result[row.folder_id].append(row.tag_id)
        return result


# ---- service ----


async def create_tag(ctx: TenantContext, req: TagCreate) -> TagOut:
    async with db_mod.sessionmaker() as session, session.begin():
        repo = TagRepository(session, ctx)
        tag = await repo.get_by_name(req.name) or await repo.create(name=req.name)
    return TagOut.model_validate(tag)


async def list_tags(ctx: TenantContext) -> list[TagOut]:
    async with db_mod.sessionmaker() as session:
        tags = await TagRepository(session, ctx).list()
    return [TagOut.model_validate(t) for t in tags]


async def delete_tag(ctx: TenantContext, tag_id: uuid.UUID) -> None:
    async with db_mod.sessionmaker() as session, session.begin():
        repo = TagRepository(session, ctx)
        tag = await repo.get_by_id(tag_id)
        if tag is None:
            raise TagNotFound("Tag not found")
        await repo.delete(tag)


async def tag_document(ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    async with db_mod.sessionmaker() as session, session.begin():
        if await DocumentRepository(session, ctx).get_by_id(document_id) is None:
            raise DocumentNotFound("Document not found")
        if await TagRepository(session, ctx).get_by_id(tag_id) is None:
            raise TagNotFound("Tag not found")
        await DocumentTagRepository(session, ctx).attach(document_id, tag_id)


async def untag_document(ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    async with db_mod.sessionmaker() as session, session.begin():
        await DocumentTagRepository(session, ctx).detach(document_id, tag_id)


async def get_tag(ctx: TenantContext, tag_id: uuid.UUID) -> TagOut:
    """Org-scoped existence lookup for OTHER modules (module-boundary rule). First
    caller: ``access_roles.service``, validating a tag exists before granting it to an
    Access Role — a plain FK can't express "in the same org," only "exists somewhere."
    """
    async with db_mod.sessionmaker() as session:
        tag = await TagRepository(session, ctx).get_by_id(tag_id)
    if tag is None:
        raise TagNotFound("Tag not found")
    return TagOut.model_validate(tag)


async def tag_folder(ctx: TenantContext, folder_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    # Local import: avoids a documents<->folders circular import at module load time,
    # same precedent as documents.py's upload_document (see memory.md).
    from app.services.documents.folders import FolderRepository

    async with db_mod.sessionmaker() as session, session.begin():
        if await FolderRepository(session, ctx).get_by_id(folder_id) is None:
            raise FolderNotFound("Folder not found")
        if await TagRepository(session, ctx).get_by_id(tag_id) is None:
            raise TagNotFound("Tag not found")
        await FolderTagRepository(session, ctx).attach(folder_id, tag_id)


async def untag_folder(ctx: TenantContext, folder_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    async with db_mod.sessionmaker() as session, session.begin():
        await FolderTagRepository(session, ctx).detach(folder_id, tag_id)


async def list_folder_tag_ids(ctx: TenantContext, folder_id: uuid.UUID) -> list[uuid.UUID]:
    async with db_mod.sessionmaker() as session:
        return await FolderTagRepository(session, ctx).list_tag_ids(folder_id)


async def list_folder_tag_ids_by_folders(
    ctx: TenantContext, folder_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    async with db_mod.sessionmaker() as session:
        return await FolderTagRepository(session, ctx).list_tag_ids_by_folders(folder_ids)


async def list_document_tag_ids_by_documents(
    ctx: TenantContext, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Narrow accessor for OTHER modules (module-boundary rule). First caller:
    ``retrieval.resolve_allowed_documents``, computing each document's own direct tags
    for the tag-based access check."""
    async with db_mod.sessionmaker() as session:
        return await DocumentTagRepository(session, ctx).list_tag_ids_by_documents(document_ids)
