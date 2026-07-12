"""Access Role routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.access_roles import AccessRoleOut

router = APIRouter(prefix="/access-roles", tags=["access-roles"])

router.post("", response_model=AccessRoleOut, status_code=201)(controllers.access_roles.create_role)
router.get("", response_model=list[AccessRoleOut])(controllers.access_roles.list_roles)
router.delete("/{role_id}", status_code=204)(controllers.access_roles.delete_role)
router.post("/{role_id}/tags/{tag_id}", status_code=204)(controllers.access_roles.grant_role_tag)
router.delete("/{role_id}/tags/{tag_id}", status_code=204)(controllers.access_roles.revoke_role_tag)
router.post("/{role_id}/users/{user_id}", status_code=204)(
    controllers.access_roles.assign_role_user
)
router.delete("/{role_id}/users/{user_id}", status_code=204)(
    controllers.access_roles.remove_role_user
)
