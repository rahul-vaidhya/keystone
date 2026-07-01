"""Folder-tree use cases: CRUD, plus F25 move/rename/delete.

Move and rename share one internal helper (``_relocate_folder``) since they have the same
correctness shape — cycle check, target-scoped name-collision check, and a same-transaction
subtree path rebuild — differing only in which field (``name`` vs ``parent_id``) changes.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.exc import IntegrityError

from app.exceptions.documents import (
    FolderCycleError,
    FolderNameConflict,
    FolderNotEmpty,
    FolderNotFound,
)
from app.models.documents import Folder
from app.platform import db as db_mod
from app.platform.context import TenantContext
from app.repositories.documents import FolderRepository
from app.schemas.documents import FolderCreate, FolderOut

# Distinguishes "leave parent_id unchanged" from "move to root" (parent_id=None is a valid,
# meaningful target — a sentinel is required so callers can express "don't touch this".
_UNCHANGED = object()


async def create_folder(ctx: TenantContext, req: FolderCreate) -> FolderOut:
    async with db_mod.sessionmaker() as session, session.begin():
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
    async with db_mod.sessionmaker() as session, session.begin():
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

    return FolderOut.model_validate(folder)


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
    async with db_mod.sessionmaker() as session, session.begin():
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
