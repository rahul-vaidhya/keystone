"""Tag + document-tag use cases."""

from __future__ import annotations

import uuid

from app.exceptions.documents import DocumentNotFound, TagNotFound
from app.platform import db as db_mod
from app.platform.context import TenantContext
from app.repositories.documents import DocumentRepository, DocumentTagRepository, TagRepository
from app.schemas.documents import TagCreate, TagOut


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
