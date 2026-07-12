"""Auth routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.auth import OrganizationOut, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

router.post("/signup", response_model=TokenResponse, status_code=201)(controllers.auth.signup)
router.post("/login", response_model=TokenResponse)(controllers.auth.login)
router.post("/refresh", response_model=TokenResponse)(controllers.auth.refresh_token)
router.post("/logout", status_code=204)(controllers.auth.logout)
router.get("/me", response_model=UserOut)(controllers.auth.me)
router.post("/invite", response_model=UserOut, status_code=201)(controllers.auth.invite)
router.get("/users", response_model=list[UserOut])(controllers.auth.list_users)
router.patch("/users/{user_id}/role", response_model=UserOut)(controllers.auth.change_role)
router.get("/org", response_model=OrganizationOut)(controllers.auth.get_org)
router.patch("/org", response_model=OrganizationOut)(controllers.auth.rename_org)
