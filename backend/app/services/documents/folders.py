"""Folder-tree use cases: CRUD, plus F25 move/rename/delete.

Move and rename share one internal helper (``_relocate_folder``) since they have the same
correctness shape — cycle check, target-scoped name-collision check, and a same-transaction
subtree path rebuild — differing only in which field (``name`` vs ``parent_id``) changes.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import Document, Folder, FolderCreate, FolderOut
from app.services.access_roles import resolve_accessible_folder_ids
from app.services.base import BaseRepository
from app.services.documents.exceptions import (
    FolderAccessDenied,
    FolderCycleError,
    FolderNameConflict,
    FolderNotEmpty,
    FolderNotFound,
)


# ---- repository ----
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


# ---- service ----
# Exceptions imported from documents.py (after merge completes)
# Distinguish "leave parent_id unchanged" from "move to root" (parent_id=None is a valid,
# meaningful target — a sentinel is required so callers can express "don't touch this".
_UNCHANGED = object()


def _to_folder_out(
    folder: Folder, tag_ids: list[uuid.UUID], *, can_manage: bool = True
) -> FolderOut:
    """``FolderOut.tag_ids`` isn't an ORM column — this codebase avoids ORM relationships
    (repository-explicit queries only), so tags are fetched separately (``FolderTagRepository``)
    and merged in here rather than via ``model_validate`` alone."""
    return FolderOut.model_validate(folder).model_copy(
        update={"tag_ids": tag_ids, "can_manage": can_manage}
    )


async def _assert_folder_access(ctx: TenantContext, folder_id: uuid.UUID) -> None:
    """Denies rename/move/delete/subfolder-create/document-move on a folder
    ``ctx.user_id``'s Access Roles don't grant them visibility into. Reuses
    ``list_folders``'s already-computed ``can_manage`` (single source of truth — see
    ``access_roles.resolve_accessible_folder_ids`` for the tag-inheritance rule itself,
    the same one ``resolve_allowed_documents`` uses for document retrieval). A
    nonexistent ``folder_id`` is a silent no-op here — the caller's own subsequent DB
    fetch raises ``FolderNotFound``, which should win over an access-denied claim about
    a folder that doesn't exist."""
    folders = await list_folders(ctx)
    target = next((f for f in folders if f.id == folder_id), None)
    if target is not None and not target.can_manage:
        raise FolderAccessDenied("You do not have access to this folder")


async def create_folder(ctx: TenantContext, req: FolderCreate) -> FolderOut:
    if req.parent_id is not None:
        await _assert_folder_access(ctx, req.parent_id)
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = FolderRepository(session, ctx)
        path = req.name
        if req.parent_id is not None:
            parent = await repo.get_by_id(req.parent_id)
            if parent is None:
                raise FolderNotFound("Parent folder not found")
            path = f"{parent.path}/{req.name}"
        # Pre-existing gap, fixed here: create_folder previously had neither this
        # application-level check nor the IntegrityError translation below, so any
        # duplicate-name create (root OR sibling, no race needed) raised an unhandled
        # IntegrityError -> 500. _relocate_folder (rename/move) already had both; this
        # brings create to the same check-then-act + constraint-backstop discipline.
        if await repo.exists_name_conflict(req.parent_id, req.name):
            raise FolderNameConflict("A folder with that name already exists here")
        try:
            folder = await repo.create(parent_id=req.parent_id, name=req.name, path=path)
        except IntegrityError as exc:
            # Residual race, same shape as _relocate_folder's: the check above and this
            # write share one transaction, but read-committed isolation doesn't fully
            # serialize two concurrent creates racing for the same (parent_id, name) — the
            # unique constraint (or, for parent_id IS NULL, the new partial unique index
            # closing the root-level gap) is the actual backstop.
            raise FolderNameConflict("A folder with that name already exists here") from exc
    return _to_folder_out(folder, [])


async def list_folders(ctx: TenantContext) -> list[FolderOut]:
    """Browsing stays open to everyone (unchanged) — ``can_manage`` is computed and
    attached per folder so the caller (frontend FolderTree, or ``_assert_folder_access``
    itself) can tell which ones this ``ctx.user_id`` may actually rename/move/delete,
    without hiding the restricted ones from the listing."""
    # Local import: avoids a documents<->folders circular import at module load time,
    # same precedent as documents.py's upload_document (see memory.md).
    from app.services.documents.tags import FolderTagRepository

    async with db_mod.tenant_session(ctx.org_id) as session:
        folders = await FolderRepository(session, ctx).list()
        tag_ids_by_folder = await FolderTagRepository(session, ctx).list_tag_ids_by_folders(
            [f.id for f in folders]
        )
    outs = [_to_folder_out(f, tag_ids_by_folder.get(f.id, [])) for f in folders]
    accessible = await resolve_accessible_folder_ids(ctx, outs)
    if accessible is None:
        return outs
    return [o.model_copy(update={"can_manage": o.id in accessible}) for o in outs]


async def get_folder(ctx: TenantContext, folder_id: uuid.UUID) -> FolderOut:
    folder = next((f for f in await list_folders(ctx) if f.id == folder_id), None)
    if folder is None:
        raise FolderNotFound("Folder not found")
    return folder


def _rebuild_subtree_paths(
    subtree: list[Folder],
    *,
    root_id: uuid.UUID,
    target_parent_id: uuid.UUID | None,
    target_parent_path: str | None,
    target_name: str,
) -> None:
    """Mutate ``path`` for every folder in ``subtree`` (and ``parent_id``/``name`` for the
    root), deriving each path strictly from its parent's freshly-rebuilt path + its own
    name — never by slicing the OLD path string. ``path`` is a non-authoritative display
    cache that may already be stale, so string surgery on it is exactly the substring-bug
    class to avoid (it also risks false-matching siblings sharing a name prefix, e.g. "HR"
    vs. "HR-Archive"). ``subtree`` must be in parent-before-child order (see
    ``FolderRepository.list_subtree``'s BFS guarantee) — a child's parent must already have
    its rebuilt path in ``new_paths`` before the child is processed.
    """
    new_paths: dict[uuid.UUID, str] = {}
    for folder in subtree:
        if folder.id == root_id:
            folder.parent_id = target_parent_id
            folder.name = target_name
            parent_path, name = target_parent_path, target_name
        else:
            parent_path, name = new_paths[folder.parent_id], folder.name
        new_path = f"{parent_path}/{name}" if parent_path else name
        new_paths[folder.id] = new_path
        folder.path = new_path


async def _relocate_folder(
    ctx: TenantContext,
    folder_id: uuid.UUID,
    *,
    new_name: str | None = None,
    new_parent_id: uuid.UUID | None | object = _UNCHANGED,
) -> FolderOut:
    from app.services.documents.tags import FolderTagRepository

    # The folder being renamed/moved must itself be accessible; a genuine move (not a
    # plain rename — new_parent_id is a real target, not the _UNCHANGED sentinel or a
    # move-to-root None) also requires access to the DESTINATION, so a member can't
    # smuggle an accessible folder into a restricted one they can't otherwise touch.
    await _assert_folder_access(ctx, folder_id)
    if new_parent_id is not _UNCHANGED and new_parent_id is not None:
        await _assert_folder_access(ctx, new_parent_id)

    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = FolderRepository(session, ctx)
        folder = await repo.get_by_id(folder_id)
        if folder is None:
            raise FolderNotFound("Folder not found")

        target_parent_id = folder.parent_id if new_parent_id is _UNCHANGED else new_parent_id
        target_name = folder.name if new_name is None else new_name

        target_parent = None
        if target_parent_id is not None:
            target_parent = await repo.get_by_id(target_parent_id)
            if target_parent is None:
                raise FolderNotFound("Target parent folder not found")

        subtree = await repo.list_subtree(folder_id)
        subtree_ids = {f.id for f in subtree}
        if target_parent_id in subtree_ids:
            raise FolderCycleError("Cannot move a folder into itself or one of its descendants")

        if await repo.exists_name_conflict(target_parent_id, target_name, exclude_id=folder.id):
            raise FolderNameConflict("A folder with that name already exists here")

        _rebuild_subtree_paths(
            subtree,
            root_id=folder.id,
            target_parent_id=target_parent_id,
            target_parent_path=target_parent.path if target_parent else None,
            target_name=target_name,
        )

        try:
            await session.flush()
        except IntegrityError as exc:
            # Residual race: the collision check above and this write share one
            # transaction, but read-committed isolation doesn't fully serialize two
            # concurrent moves racing for the same (parent_id, name) — the unique
            # constraint is the actual backstop. Translate it to the same 409 the
            # application-level check would have raised, rather than leaking a 500.
            raise FolderNameConflict("A folder with that name already exists here") from exc

        tag_ids = await FolderTagRepository(session, ctx).list_tag_ids(folder_id)
    return _to_folder_out(folder, tag_ids)


async def rename_folder(ctx: TenantContext, folder_id: uuid.UUID, new_name: str) -> FolderOut:
    return await _relocate_folder(ctx, folder_id, new_name=new_name)


async def move_folder(
    ctx: TenantContext, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None
) -> FolderOut:
    return await _relocate_folder(ctx, folder_id, new_parent_id=new_parent_id)


DeleteMode = Literal["block", "cascade", "reflow"]


async def delete_folder(
    ctx: TenantContext, folder_id: uuid.UUID, *, mode: DeleteMode = "block"
) -> None:
    await _assert_folder_access(ctx, folder_id)
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = FolderRepository(session, ctx)
        folder = await repo.get_by_id(folder_id)
        if folder is None:
            raise FolderNotFound("Folder not found")

        if mode == "block":
            if await repo.has_children(folder_id) or await repo.has_documents(folder_id):
                raise FolderNotEmpty(
                    "Folder is not empty — pass mode=cascade or mode=reflow to delete it anyway"
                )
            await repo.delete(folder)

        elif mode == "cascade":
            # Deliberate, confirmed behavior (architecture.md: "folder is NOT a permission
            # boundary") — the DB's ON DELETE CASCADE on Folder.parent_id removes the whole
            # descendant folder subtree; Document.folder_id's ON DELETE SET NULL means every
            # document anywhere in that subtree survives, orphaned to org root. Documents
            # are never deleted by a folder operation. One row delete is sufficient — the
            # existing FK constraints do the rest.
            await repo.delete(folder)

        else:  # mode == "reflow"
            new_parent_id = folder.parent_id
            for child in await repo.list_direct_children(folder_id):
                # Each direct child folder moves up one level — same correctness rules as
                # an explicit move (collision-checked, path-rebuilt), reusing the same
                # subtree-rebuild helper so a child with its own descendants is handled
                # correctly too.
                child_subtree = await repo.list_subtree(child.id)
                if await repo.exists_name_conflict(new_parent_id, child.name, exclude_id=child.id):
                    raise FolderNameConflict(
                        f"Cannot reflow: a folder named '{child.name}' already exists "
                        "in the parent folder"
                    )
                new_parent = (
                    await repo.get_by_id(new_parent_id) if new_parent_id is not None else None
                )
                _rebuild_subtree_paths(
                    child_subtree,
                    root_id=child.id,
                    target_parent_id=new_parent_id,
                    target_parent_path=new_parent.path if new_parent else None,
                    target_name=child.name,
                )
            await repo.reparent_documents(folder_id, new_parent_id)
            try:
                await repo.delete(folder)
                await session.flush()
            except IntegrityError as exc:
                raise FolderNameConflict(
                    "A folder with that name already exists in the parent folder"
                ) from exc
