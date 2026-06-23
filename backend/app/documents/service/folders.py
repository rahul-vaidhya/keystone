"""Folder-tree use cases."""

from __future__ import annotations

import uuid

from app.documents.exceptions import FolderNotFound
from app.documents.repository import FolderRepository
from app.documents.schemas import FolderCreate, FolderOut
from app.platform import db as db_mod
from app.platform.context import TenantContext


async def create_folder(ctx: TenantContext, req: FolderCreate) -> FolderOut:
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


async def list_folders(ctx: TenantContext) -> list[FolderOut]:
    async with db_mod.sessionmaker() as session:
        folders = await FolderRepository(session, ctx).list()
    return [FolderOut.model_validate(f) for f in folders]


async def get_folder(ctx: TenantContext, folder_id: uuid.UUID) -> FolderOut:
    async with db_mod.sessionmaker() as session:
        folder = await FolderRepository(session, ctx).get_by_id(folder_id)
    if folder is None:
        raise FolderNotFound("Folder not found")
    return FolderOut.model_validate(folder)


async def delete_folder(ctx: TenantContext, folder_id: uuid.UUID) -> None:
    async with db_mod.sessionmaker() as session, session.begin():
        repo = FolderRepository(session, ctx)
        folder = await repo.get_by_id(folder_id)
        if folder is None:
            raise FolderNotFound("Folder not found")
        await repo.delete(folder)
