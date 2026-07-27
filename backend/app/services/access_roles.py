"""Access Role use cases — custom, tag-based resource-access grants (docs/
access-roles-dnd-plan.md). Deliberately separate from the system role (owner/admin/
member); reaches other modules only through their ``service`` (module-boundary rule):
``documents_service`` to validate a tag exists before granting it, ``auth_service`` to
validate a user exists before assigning them a role. ``retrieval.py`` calls back into
THIS module's service for the tag-grant lookups it needs.
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.access_roles import (
    AccessRole,
    AccessRoleCreate,
    AccessRoleOut,
    AccessRoleTag,
    UserAccessRole,
)
from app.models.documents import FolderOut
from app.services.base import BaseRepository
from app.utils.constants import ADMIN_ROLES


# ---- exceptions ----
class AccessRolesError(Exception):
    """Base Access Role failure."""


class AccessRoleNotFound(AccessRolesError):
    pass


class AccessRoleNameConflict(AccessRolesError):
    pass


# ---- repository ----
class AccessRoleRepository(BaseRepository[AccessRole]):
    model = AccessRole

    async def list(self) -> list[AccessRole]:
        stmt = self._scoped().order_by(AccessRole.name)
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, role_id: uuid.UUID) -> AccessRole | None:
        stmt = self._scoped().where(AccessRole.id == role_id)
        return await self._db.scalar(stmt)

    async def exists_name_conflict(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = self._scoped().where(AccessRole.name == name)
        if exclude_id is not None:
            stmt = stmt.where(AccessRole.id != exclude_id)
        return await self._db.scalar(stmt) is not None

    async def create(self, *, name: str) -> AccessRole:
        role = AccessRole(org_id=self._ctx.org_id, name=name)
        self._db.add(role)
        await self._db.flush()
        return role

    async def delete(self, role: AccessRole) -> None:
        await self._db.delete(role)


class AccessRoleTagRepository(BaseRepository[AccessRoleTag]):
    model = AccessRoleTag

    async def attach(self, role_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        stmt = (
            pg_insert(AccessRoleTag)
            .values(org_id=self._ctx.org_id, access_role_id=role_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["access_role_id", "tag_id"])
        )
        await self._db.execute(stmt)

    async def detach(self, role_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        stmt = self._scoped().where(
            AccessRoleTag.access_role_id == role_id, AccessRoleTag.tag_id == tag_id
        )
        link = await self._db.scalar(stmt)
        if link is not None:
            await self._db.delete(link)

    async def list_tag_ids(self, role_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = self._scoped().where(AccessRoleTag.access_role_id == role_id)
        return [row.tag_id for row in await self._db.scalars(stmt)]

    async def list_tag_ids_by_roles(
        self, role_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        if not role_ids:
            return {}
        stmt = self._scoped().where(AccessRoleTag.access_role_id.in_(role_ids))
        result: dict[uuid.UUID, list[uuid.UUID]] = {rid: [] for rid in role_ids}
        for row in await self._db.scalars(stmt):
            result[row.access_role_id].append(row.tag_id)
        return result

    async def list_all_granted_tag_ids(self) -> set[uuid.UUID]:
        """Org-wide: every tag ID that has EVER been granted to ANY Access Role — the
        definition of "access-controlling tag" retrieval checks resources against."""
        return {row.tag_id for row in await self._db.scalars(self._scoped())}


class UserAccessRoleRepository(BaseRepository[UserAccessRole]):
    model = UserAccessRole

    async def attach(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        stmt = (
            pg_insert(UserAccessRole)
            .values(org_id=self._ctx.org_id, user_id=user_id, access_role_id=role_id)
            .on_conflict_do_nothing(index_elements=["user_id", "access_role_id"])
        )
        await self._db.execute(stmt)

    async def detach(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        stmt = self._scoped().where(
            UserAccessRole.user_id == user_id, UserAccessRole.access_role_id == role_id
        )
        link = await self._db.scalar(stmt)
        if link is not None:
            await self._db.delete(link)

    async def list_user_ids(self, role_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = self._scoped().where(UserAccessRole.access_role_id == role_id)
        return [row.user_id for row in await self._db.scalars(stmt)]

    async def list_user_ids_by_roles(
        self, role_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        if not role_ids:
            return {}
        stmt = self._scoped().where(UserAccessRole.access_role_id.in_(role_ids))
        result: dict[uuid.UUID, list[uuid.UUID]] = {rid: [] for rid in role_ids}
        for row in await self._db.scalars(stmt):
            result[row.access_role_id].append(row.user_id)
        return result

    async def list_role_ids_for_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = self._scoped().where(UserAccessRole.user_id == user_id)
        return [row.access_role_id for row in await self._db.scalars(stmt)]


# ---- service ----


def _to_access_role_out(
    role: AccessRole, tag_ids: list[uuid.UUID], user_ids: list[uuid.UUID]
) -> AccessRoleOut:
    return AccessRoleOut.model_validate(role).model_copy(
        update={"tag_ids": tag_ids, "user_ids": user_ids}
    )


async def create_access_role(ctx: TenantContext, req: AccessRoleCreate) -> AccessRoleOut:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = AccessRoleRepository(session, ctx)
        if await repo.exists_name_conflict(req.name):
            raise AccessRoleNameConflict("An Access Role with that name already exists")
        try:
            role = await repo.create(name=req.name)
        except IntegrityError as exc:
            # Residual race, same shape as folders'/tags' create — the check above and
            # this write share one transaction, but read-committed isolation doesn't
            # fully serialize two concurrent creates; the unique constraint backstops it.
            raise AccessRoleNameConflict("An Access Role with that name already exists") from exc
    return _to_access_role_out(role, [], [])


async def list_access_roles(ctx: TenantContext) -> list[AccessRoleOut]:
    async with db_mod.tenant_session(ctx.org_id) as session:
        roles = await AccessRoleRepository(session, ctx).list()
        role_ids = [r.id for r in roles]
        tag_ids_by_role = await AccessRoleTagRepository(session, ctx).list_tag_ids_by_roles(
            role_ids
        )
        user_ids_by_role = await UserAccessRoleRepository(session, ctx).list_user_ids_by_roles(
            role_ids
        )
    return [
        _to_access_role_out(r, tag_ids_by_role.get(r.id, []), user_ids_by_role.get(r.id, []))
        for r in roles
    ]


async def delete_access_role(ctx: TenantContext, role_id: uuid.UUID) -> None:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = AccessRoleRepository(session, ctx)
        role = await repo.get_by_id(role_id)
        if role is None:
            raise AccessRoleNotFound("Access Role not found")
        await repo.delete(role)


async def grant_tag(ctx: TenantContext, role_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    # Cross-module validation goes through the service, never the repository directly
    # (module-boundary rule) — and runs BEFORE opening our own transaction, since it's a
    # read-only existence check, not something that needs to share a transaction with it.
    from app.services.documents import documents_service

    await documents_service.get_tag(ctx, tag_id)

    async with db_mod.tenant_session(ctx.org_id) as session:
        if await AccessRoleRepository(session, ctx).get_by_id(role_id) is None:
            raise AccessRoleNotFound("Access Role not found")
        await AccessRoleTagRepository(session, ctx).attach(role_id, tag_id)


async def revoke_tag(ctx: TenantContext, role_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    async with db_mod.tenant_session(ctx.org_id) as session:
        await AccessRoleTagRepository(session, ctx).detach(role_id, tag_id)


async def assign_user(ctx: TenantContext, role_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from app.services.auth import auth_service

    await auth_service.get_user(ctx, user_id)

    async with db_mod.tenant_session(ctx.org_id) as session:
        if await AccessRoleRepository(session, ctx).get_by_id(role_id) is None:
            raise AccessRoleNotFound("Access Role not found")
        await UserAccessRoleRepository(session, ctx).attach(user_id, role_id)


async def remove_user(ctx: TenantContext, role_id: uuid.UUID, user_id: uuid.UUID) -> None:
    async with db_mod.tenant_session(ctx.org_id) as session:
        await UserAccessRoleRepository(session, ctx).detach(user_id, role_id)


async def resolve_user_granted_tags(ctx: TenantContext) -> set[uuid.UUID]:
    """The tags ``ctx.user_id`` can see via any Access Role they hold — one of the two
    accessors ``retrieval.resolve_allowed_documents`` needs (the other is
    ``resolve_access_controlling_tags``)."""
    if ctx.user_id is None:
        return set()
    async with db_mod.tenant_session(ctx.org_id) as session:
        role_ids = await UserAccessRoleRepository(session, ctx).list_role_ids_for_user(ctx.user_id)
        if not role_ids:
            return set()
        tag_ids_by_role = await AccessRoleTagRepository(session, ctx).list_tag_ids_by_roles(
            role_ids
        )
    granted: set[uuid.UUID] = set()
    for tag_ids in tag_ids_by_role.values():
        granted.update(tag_ids)
    return granted


async def resolve_access_controlling_tags(ctx: TenantContext) -> set[uuid.UUID]:
    """Every tag that has EVER been granted to ANY Access Role in this org — a resource
    carrying none of these tags is open to everyone, regardless of role."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await AccessRoleTagRepository(session, ctx).list_all_granted_tag_ids()


def resolve_folder_effective_tags(folders: list[FolderOut]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """A folder's effective (inherited) tag set: its own direct tags UNION its parent's
    already-computed effective set. Moved here (was ``retrieval._inherited_folder_tags``,
    private) so both ``resolve_allowed_documents`` (document visibility) and
    ``resolve_accessible_folder_ids`` (folder mutation gating) share one computation
    instead of two copies drifting apart. ``folders`` must be parent-before-child
    ordered (``documents_service.list_folders``'s materialized-``path`` sort already
    guarantees this — a child's path always sorts after its parent's)."""
    effective: dict[uuid.UUID, set[uuid.UUID]] = {}
    for folder in folders:
        parent_tags = effective.get(folder.parent_id, set()) if folder.parent_id else set()
        effective[folder.id] = set(folder.tag_ids) | parent_tags
    return effective


async def resolve_accessible_folder_ids(
    ctx: TenantContext, folders: list[FolderOut]
) -> set[uuid.UUID] | None:
    """The folder-side counterpart to ``resolve_allowed_documents`` — same tag-gating
    rule, applied to a folder's own effective tag set instead of a document's
    folder-inherited-tags ∪ direct-tags. Used to gate folder MUTATION (rename/move/
    delete/create-subfolder/document-move-in-or-out), never browsing — ``GET
    /documents/folders`` stays open to everyone, by the same "browsing is open, only
    the gated action is checked" convention resolve_allowed_documents already
    established for retrieval vs. document browsing.

    Returns ``None`` when EVERYONE can access every folder (owner/admin, or no tag has
    ever been granted to any Access Role) — the common case, and the same short-circuit
    ``resolve_allowed_documents`` takes. Otherwise returns the set of folder ids
    ``ctx.user_id`` may touch; callers must explicitly deny anything not in that set.
    """
    if ctx.role in ADMIN_ROLES:
        return None
    access_controlling = await resolve_access_controlling_tags(ctx)
    if not access_controlling:
        return None
    effective_tags = resolve_folder_effective_tags(folders)
    user_granted = await resolve_user_granted_tags(ctx)
    allowed: set[uuid.UUID] = set()
    for folder in folders:
        gating = effective_tags.get(folder.id, set()) & access_controlling
        if not gating or (gating & user_granted):
            allowed.add(folder.id)
    return allowed
