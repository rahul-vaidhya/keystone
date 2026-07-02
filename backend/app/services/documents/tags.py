"""Tag + document-tag use cases."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import DocumentTag, Tag, TagCreate, TagOut
from app.services.base import BaseRepository
from app.services.documents.documents import DocumentRepository
from app.services.documents.exceptions import DocumentNotFound, TagNotFound


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
