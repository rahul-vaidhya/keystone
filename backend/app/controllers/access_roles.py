"""Access Role HTTP handlers — thin; all logic in service. Every write here is
admin/owner-only (``require_admin``), mirroring the existing invite/change-role gate in
``controllers/auth.py`` — Access Role management is a system-management action even
though what it grants is resource-level."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import require_admin
from app.models.access_roles import AccessRoleCreate, AccessRoleOut
from app.services.access_roles import (
    assign_user,
    create_access_role,
    delete_access_role,
    grant_tag,
    list_access_roles,
    remove_user,
    revoke_tag,
)


async def create_role(
    req: AccessRoleCreate, ctx: Annotated[TenantContext, Depends(require_admin)]
) -> AccessRoleOut:
    return await create_access_role(ctx, req)


async def list_roles(
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> list[AccessRoleOut]:
    return await list_access_roles(ctx)


async def delete_role(
    role_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(require_admin)]
) -> None:
    await delete_access_role(ctx, role_id)


async def grant_role_tag(
    role_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await grant_tag(ctx, role_id, tag_id)


async def revoke_role_tag(
    role_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await revoke_tag(ctx, role_id, tag_id)


async def assign_role_user(
    role_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await assign_user(ctx, role_id, user_id)


async def remove_role_user(
    role_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await remove_user(ctx, role_id, user_id)
