"""Identity auth use cases."""

from __future__ import annotations

import uuid

from app.exceptions.auth import (
    AmbiguousLogin,
    EmailTaken,
    Forbidden,
    InvalidCredentials,
    TargetUserNotFound,
    UserNotFound,
)
from app.platform import db as db_mod
from app.platform.constants import ADMIN_ROLES, ROLE_OWNER, ROLES
from app.platform.context import TenantContext
from app.platform.passwords import hash_password, verify_password
from app.platform.tokens import issue_access_token, issue_refresh_token
from app.repositories.auth import AuthRepository, OrganizationRepository, UserRepository
from app.schemas.auth import InviteRequest, LoginRequest, SignupRequest, TokenResponse, UserOut


class AuthService:
    async def signup(self, req: SignupRequest) -> tuple[TokenResponse, str]:
        async with db_mod.sessionmaker() as session, session.begin():
            auth_repo = AuthRepository(session)
            if await auth_repo.email_exists_globally(req.email):
                raise EmailTaken("Email already registered")

            org_repo = OrganizationRepository(session)
            org = await org_repo.create(req.org_name)
            user = await auth_repo.create_user(
                org_id=org.id,
                email=req.email,
                password_hash=hash_password(req.password),
                role=ROLE_OWNER,
            )

        access = issue_access_token(
            user_id=user.id, org_id=user.org_id, role=user.role, email=user.email
        )
        refresh = issue_refresh_token(user_id=user.id, org_id=user.org_id)
        return TokenResponse(access_token=access), refresh

    async def login(self, req: LoginRequest) -> tuple[TokenResponse, str]:
        async with db_mod.sessionmaker() as session:
            auth_repo = AuthRepository(session)
            candidates = await auth_repo.find_login_candidates(req.email)

        if not candidates:
            raise InvalidCredentials("Invalid email or password")

        if len(candidates) > 1 and req.org_id is None:
            raise AmbiguousLogin(
                [{"org_id": user.org_id, "org_name": org_name} for user, org_name in candidates]
            )

        if req.org_id is not None:
            matched = [(u, n) for u, n in candidates if u.org_id == req.org_id]
            if not matched:
                raise InvalidCredentials("Invalid email or password")
            user, _ = matched[0]
        else:
            user, _ = candidates[0]

        if not user.password_hash or not verify_password(req.password, user.password_hash):
            raise InvalidCredentials("Invalid email or password")

        access = issue_access_token(
            user_id=user.id, org_id=user.org_id, role=user.role, email=user.email
        )
        refresh = issue_refresh_token(user_id=user.id, org_id=user.org_id)
        return TokenResponse(access_token=access), refresh

    async def refresh(self, refresh_token: str) -> tuple[TokenResponse, str]:
        from app.platform.tokens import TokenError, decode_refresh_token

        try:
            payload = decode_refresh_token(refresh_token)
        except TokenError as exc:
            raise InvalidCredentials("Invalid refresh token") from exc

        user_id = uuid.UUID(payload["sub"])
        async with db_mod.sessionmaker() as session:
            auth_repo = AuthRepository(session)
            user = await auth_repo.get_user_by_id(user_id)

        if user is None or not user.password_hash:
            raise InvalidCredentials("Invalid refresh token")

        access = issue_access_token(
            user_id=user.id, org_id=user.org_id, role=user.role, email=user.email
        )
        new_refresh = issue_refresh_token(user_id=user.id, org_id=user.org_id)
        return TokenResponse(access_token=access), new_refresh

    async def me(self, user_id: uuid.UUID) -> UserOut:
        async with db_mod.sessionmaker() as session:
            auth_repo = AuthRepository(session)
            user = await auth_repo.get_user_by_id(user_id)
        if user is None:
            raise UserNotFound("User not found")
        return UserOut.model_validate(user)

    async def invite(self, ctx: TenantContext, req: InviteRequest) -> UserOut:
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can invite users")
        if req.role not in ROLES or req.role == ROLE_OWNER:
            raise Forbidden("Invalid invite role")

        async with db_mod.sessionmaker() as session, session.begin():
            user_repo = UserRepository(session, ctx)
            if await user_repo.get_by_email(req.email):
                raise EmailTaken("Email already in this organization")

            auth_repo = AuthRepository(session)
            user = await auth_repo.create_user(
                org_id=ctx.org_id,
                email=req.email,
                password_hash=hash_password(req.password),
                role=req.role,
            )

        return UserOut.model_validate(user)

    async def list_org_users(self, ctx: TenantContext) -> list[UserOut]:
        async with db_mod.sessionmaker() as session:
            user_repo = UserRepository(session, ctx)
            users = await user_repo.list()
        return [UserOut.model_validate(u) for u in users]

    async def change_role(
        self, ctx: TenantContext, target_user_id: uuid.UUID, new_role: str
    ) -> UserOut:
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can change roles")
        if new_role not in ROLES or new_role == ROLE_OWNER:
            raise Forbidden("Invalid role")
        if target_user_id == ctx.user_id:
            raise Forbidden("Cannot change your own role")

        async with db_mod.sessionmaker() as session, session.begin():
            user_repo = UserRepository(session, ctx)
            target = await user_repo.get_by_id(target_user_id)
            if target is None:
                raise TargetUserNotFound("User not found")
            if target.role == ROLE_OWNER:
                raise Forbidden("Cannot change an owner's role")

            updated = await user_repo.update_role(target, new_role)

        return UserOut.model_validate(updated)


auth_service = AuthService()
