"""Identity auth use cases."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.auth import (
    InviteRequest,
    LoginRequest,
    Organization,
    OrganizationOut,
    SignupRequest,
    TokenResponse,
    User,
    UserOut,
)
from app.services.base import BaseRepository
from app.utils.constants import ADMIN_ROLES, ROLE_OWNER, ROLES
from app.utils.passwords import hash_password, verify_password
from app.utils.tokens import issue_access_token, issue_refresh_token


# ---- exceptions ----
class AuthError(Exception):
    """Base auth failure."""


class InvalidCredentials(AuthError):
    pass


class EmailTaken(AuthError):
    pass


class UserNotFound(AuthError):
    pass


class AmbiguousLogin(AuthError):
    """Same email exists in multiple orgs — caller must disambiguate."""

    def __init__(self, org_choices: list[dict[str, object]]) -> None:
        self.org_choices = org_choices
        super().__init__("Email exists in multiple organizations")


class Forbidden(AuthError):
    pass


class TargetUserNotFound(AuthError):
    pass


# ---- repository ----
class OrganizationRepository:
    """Repository for the tenancy root — no ``org_id`` column on ``organizations``."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create(self, name: str) -> Organization:
        org = Organization(name=name)
        self._db.add(org)
        await self._db.flush()
        return org

    async def get(self, org_id: uuid.UUID) -> Organization | None:
        return await self._db.get(Organization, org_id)

    async def update_name(self, org: Organization, name: str) -> Organization:
        org.name = name
        await self._db.flush()
        return org


class AuthRepository:
    """Unscoped auth reads/writes — only for signup, login, and token validation."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def find_login_candidates(self, email: str) -> list[tuple[User, str]]:
        stmt = (
            select(User, Organization.name)
            .join(Organization, User.org_id == Organization.id)
            .where(User.email == email)
        )
        return list((await self._db.execute(stmt)).all())

    async def email_exists_globally(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email).limit(1)
        return (await self._db.scalar(stmt)) is not None

    async def create_user(
        self,
        *,
        org_id: uuid.UUID,
        email: str,
        password_hash: str,
        role: str,
    ) -> User:
        user = User(org_id=org_id, email=email, password_hash=password_hash, role=role)
        self._db.add(user)
        await self._db.flush()
        return user

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._db.get(User, user_id)


class UserRepository(BaseRepository[User]):
    model = User

    async def list(self) -> list[User]:
        """All users in the caller's org (app-level ``org_id`` filter applied by base)."""
        result = await self._db.scalars(self._scoped())
        return list(result)

    async def get_by_email(self, email: str) -> User | None:
        stmt = self._scoped().where(User.email == email)
        return await self._db.scalar(stmt)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = self._scoped().where(User.id == user_id)
        return await self._db.scalar(stmt)

    async def update_role(self, user: User, role: str) -> User:
        user.role = role
        await self._db.flush()
        return user


# ---- service ----


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
        from app.utils.tokens import TokenError, decode_refresh_token

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

    async def get_user(self, ctx: TenantContext, user_id: uuid.UUID) -> UserOut:
        """Org-scoped existence lookup for OTHER modules (module-boundary rule). First
        caller: ``access_roles.service``, validating a member exists in this org before
        assigning them an Access Role."""
        async with db_mod.sessionmaker() as session:
            user = await UserRepository(session, ctx).get_by_id(user_id)
        if user is None:
            raise TargetUserNotFound("User not found")
        return UserOut.model_validate(user)

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

    async def get_org(self, ctx: TenantContext) -> OrganizationOut:
        async with db_mod.sessionmaker() as session:
            org = await OrganizationRepository(session).get(ctx.org_id)
        return OrganizationOut.model_validate(org)

    async def rename_org(self, ctx: TenantContext, new_name: str) -> OrganizationOut:
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can rename the organization")

        async with db_mod.sessionmaker() as session, session.begin():
            org_repo = OrganizationRepository(session)
            org = await org_repo.get(ctx.org_id)
            updated = await org_repo.update_name(org, new_name)

        return OrganizationOut.model_validate(updated)


auth_service = AuthService()
