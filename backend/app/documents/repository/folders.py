"""``folders`` table — all SQL for the folder tree (codestandards "Layering")."""

from __future__ import annotations

import uuid

from app.documents.models import Folder
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
