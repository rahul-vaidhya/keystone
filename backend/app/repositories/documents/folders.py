"""``folders`` table — all SQL for the folder tree (codestandards "Layering")."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.documents import Document, Folder
from app.platform.repository import BaseRepository


class FolderRepository(BaseRepository[Folder]):
    model = Folder

    async def list(self) -> list[Folder]:
        stmt = self._scoped().order_by(Folder.path)
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, folder_id: uuid.UUID) -> Folder | None:
        stmt = self._scoped().where(Folder.id == folder_id)
        return await self._db.scalar(stmt)

    async def list_direct_children(self, folder_id: uuid.UUID) -> list[Folder]:
        stmt = self._scoped().where(Folder.parent_id == folder_id)
        return list(await self._db.scalars(stmt))

    async def has_children(self, folder_id: uuid.UUID) -> bool:
        stmt = self._scoped().where(Folder.parent_id == folder_id)
        return await self._db.scalar(stmt) is not None

    async def has_documents(self, folder_id: uuid.UUID) -> bool:
        stmt = select(Document).where(
            Document.org_id == self._ctx.org_id, Document.folder_id == folder_id
        )
        return await self._db.scalar(stmt) is not None

    async def exists_name_conflict(
        self, parent_id: uuid.UUID | None, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        stmt = self._scoped().where(Folder.parent_id == parent_id, Folder.name == name)
        if exclude_id is not None:
            stmt = stmt.where(Folder.id != exclude_id)
        return await self._db.scalar(stmt) is not None

    async def list_subtree(self, folder_id: uuid.UUID) -> list[Folder]:
        """Breadth-first traversal of ``folder_id`` and every descendant, org-scoped.

        Returned order is root-first then level-by-level — guarantees every folder's
        parent already appears earlier in the list. Callers rebuilding ``path`` top-down
        rely on this ordering (a parent's new path must exist before a child's is derived
        from it). BFS gives this for free with no separate depth column: each round only
        ever queries the previous round's children, so a folder can never appear before
        the round that produced its parent.
        """
        root = await self.get_by_id(folder_id)
        if root is None:
            return []
        result = [root]
        frontier_ids = [folder_id]
        while frontier_ids:
            stmt = self._scoped().where(Folder.parent_id.in_(frontier_ids))
            children = list(await self._db.scalars(stmt))
            if not children:
                break
            result.extend(children)
            frontier_ids = [c.id for c in children]
        return result

    async def reparent_documents(
        self, old_folder_id: uuid.UUID, new_folder_id: uuid.UUID | None
    ) -> None:
        """Re-point every document directly in ``old_folder_id`` to ``new_folder_id``
        (used by reflow-delete — only direct documents move; documents inside child
        folders stay with their (separately reflowed) folder)."""
        stmt = select(Document).where(
            Document.org_id == self._ctx.org_id, Document.folder_id == old_folder_id
        )
        for doc in await self._db.scalars(stmt):
            doc.folder_id = new_folder_id
        await self._db.flush()

    async def create(self, *, parent_id: uuid.UUID | None, name: str, path: str) -> Folder:
        folder = Folder(org_id=self._ctx.org_id, parent_id=parent_id, name=name, path=path)
        self._db.add(folder)
        await self._db.flush()
        return folder

    async def delete(self, folder: Folder) -> None:
        await self._db.delete(folder)
