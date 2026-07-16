"""Identity auth use cases."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.auth import (
    AcceptInviteRequest,
    InviteOut,
    InviteRequest,
    InviteToken,
    LoginRequest,
    Organization,
    OrganizationOut,
    SignupRequest,
    TokenResponse,
    User,
    UserOut,
)
from app.services.base import BaseRepository
from app.utils.constants import (
    ADMIN_ROLES,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_LOCKOUT_THRESHOLD,
    ROLE_OWNER,
    ROLES,
)
from app.utils.passwords import hash_password, verify_password
from app.utils.tokens import issue_access_token, issue_refresh_token

# Self-serve invite links (replaces the admin-typed-initial-password flow) expire 7
# days after creation — a reasonable fixed default, not worth a settings field for one
# constant.
_INVITE_TOKEN_TTL_DAYS = 7


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


class AccountLocked(AuthError):
    """Too many failed login attempts — locked until ``locked_until``."""

    def __init__(self, locked_until: datetime) -> None:
        self.locked_until = locked_until
        super().__init__("Account temporarily locked")


class InvalidInviteToken(AuthError):
    """One generic exception/message covers not-found, wrong-org, expired, AND
    already-used — mirroring this file's own ``InvalidCredentials`` precedent of
    collapsing distinct failure causes into one message to avoid giving an attacker an
    oracle (e.g. distinguishing "expired" from "already used" would leak whether a
    given link was ever valid)."""


# ---- repository ----
class OrganizationRepository:
    """Repository for the tenancy root — no ``org_id`` column on ``organizations``."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create(self, name: str, *, org_id: uuid.UUID | None = None) -> Organization:
        """``org_id`` is passed by signup (F60): the tenant GUC must be set to the new
        org's id BEFORE this INSERT so the RLS ``WITH CHECK`` passes, which means the id
        has to exist before the row does — client-generated, overriding the column's
        ``gen_random_uuid()`` server default."""
        org = Organization(name=name) if org_id is None else Organization(id=org_id, name=name)
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
        """Active-only — a deactivated member's org doesn't even show up as a login
        choice, so a removed user gets the same generic failure as a wrong password."""
        stmt = (
            select(User, Organization.name)
            .join(Organization, User.org_id == Organization.id)
            .where(User.email == email, User.is_active.is_(True))
        )
        return list((await self._db.execute(stmt)).all())

    async def record_login_failure(self, user: User) -> None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= LOGIN_LOCKOUT_THRESHOLD:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        await self._db.flush()

    async def reset_login_lockout(self, user: User) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        await self._db.flush()

    async def email_exists_globally(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email).limit(1)
        return (await self._db.scalar(stmt)) is not None

    async def create_user(
        self,
        *,
        org_id: uuid.UUID,
        email: str,
        password_hash: str | None,
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

    async def set_active(self, user: User, is_active: bool) -> User:
        user.is_active = is_active
        await self._db.flush()
        return user

    async def update_password(self, user: User, password_hash: str) -> User:
        user.password_hash = password_hash
        # Bumping token_version invalidates every OTHER session (every already-issued
        # access/refresh token carries the OLD version in its "tv" claim and will fail
        # the check in current_user/refresh) without needing a token denylist. Harmless
        # here too when called from accept-invite: a brand-new user has no prior
        # sessions to invalidate.
        user.token_version += 1
        await self._db.flush()
        return user


class InviteTokenRepository(BaseRepository[InviteToken]):
    model = InviteToken

    async def create(
        self, *, user_id: uuid.UUID, expires_at: datetime, token_hash: str
    ) -> InviteToken:
        invite = InviteToken(
            org_id=self._ctx.org_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._db.add(invite)
        await self._db.flush()
        return invite

    async def get_valid_by_hash(self, token_hash: str) -> InviteToken | None:
        """Org-scoped (via ``_scoped()``) plus unused plus unexpired. Deliberately does
        NOT distinguish "not found" from "expired" from "used" at the SQL layer — the
        service raises one generic ``InvalidInviteToken`` for all of them."""
        stmt = self._scoped().where(
            InviteToken.token_hash == token_hash,
            InviteToken.used_at.is_(None),
            InviteToken.expires_at > datetime.now(UTC),
        )
        return await self._db.scalar(stmt)

    async def mark_used(self, invite: InviteToken) -> None:
        invite.used_at = datetime.now(UTC)
        await self._db.flush()


# ---- service ----


class AuthService:
    async def signup(self, req: SignupRequest) -> tuple[TokenResponse, str]:
        # Pre-tenant bootstrap (F60): no org exists yet, so this opens auth_session —
        # the app.auth_email GUC lets the global email-uniqueness check see exactly the
        # rows matching this email across orgs, nothing else. The new org's id is then
        # generated CLIENT-side and the tenant GUC switched to it before any INSERT, so
        # the org + owner rows pass their tenant_isolation WITH CHECK.
        async with db_mod.auth_session(req.email) as session:
            auth_repo = AuthRepository(session)
            if await auth_repo.email_exists_globally(req.email):
                raise EmailTaken("Email already registered")

            new_org_id = uuid.uuid4()
            await db_mod.set_org_guc(session, new_org_id)
            org_repo = OrganizationRepository(session)
            org = await org_repo.create(req.org_name, org_id=new_org_id)
            user = await auth_repo.create_user(
                org_id=org.id,
                email=req.email,
                password_hash=hash_password(req.password),
                role=ROLE_OWNER,
            )

        access = issue_access_token(
            user_id=user.id,
            org_id=user.org_id,
            role=user.role,
            email=user.email,
            token_version=user.token_version,
        )
        refresh = issue_refresh_token(
            user_id=user.id, org_id=user.org_id, token_version=user.token_version
        )
        return TokenResponse(access_token=access), refresh

    async def login(self, req: LoginRequest) -> tuple[TokenResponse, str]:
        # A failed attempt must persist even though we raise afterward — raising
        # INSIDE `session.begin()` rolls back that write, so the intended failure is
        # captured here and raised only after the block commits.
        pending_error: AuthError | None = None
        user: User | None = None

        # Pre-tenant bootstrap (F60): the cross-org candidate search runs under the
        # app.auth_email GUC (users matching this email + their orgs' names for
        # AmbiguousLogin — the two auth_email_lookup policies from migration 0015).
        async with db_mod.auth_session(req.email) as session:
            auth_repo = AuthRepository(session)
            candidates = await auth_repo.find_login_candidates(req.email)

            if not candidates:
                raise InvalidCredentials("Invalid email or password")

            if len(candidates) > 1 and req.org_id is None:
                raise AmbiguousLogin(
                    [{"org_id": u.org_id, "org_name": org_name} for u, org_name in candidates]
                )

            if req.org_id is not None:
                matched = [(u, n) for u, n in candidates if u.org_id == req.org_id]
                if not matched:
                    raise InvalidCredentials("Invalid email or password")
                user, _ = matched[0]
            else:
                user, _ = candidates[0]

            # Candidate matched → switch INTO that org's tenant scope for the rest of
            # the transaction: the lockout-counter/lock-reset UPDATEs below run under
            # the ordinary tenant_isolation policy (auth_email_lookup is SELECT-only).
            await db_mod.set_org_guc(session, user.org_id)

            now = datetime.now(UTC)
            if user.locked_until is not None and user.locked_until > now:
                raise AccountLocked(user.locked_until)
            if user.locked_until is not None:
                # Lock window has passed — start a fresh observation window instead of
                # leaving a stale counter that would re-lock on a single failure.
                user.failed_login_attempts = 0
                user.locked_until = None

            if not user.password_hash or not verify_password(req.password, user.password_hash):
                await auth_repo.record_login_failure(user)
                pending_error = InvalidCredentials("Invalid email or password")
            else:
                await auth_repo.reset_login_lockout(user)

        if pending_error is not None:
            raise pending_error
        assert user is not None

        access = issue_access_token(
            user_id=user.id,
            org_id=user.org_id,
            role=user.role,
            email=user.email,
            token_version=user.token_version,
        )
        refresh = issue_refresh_token(
            user_id=user.id, org_id=user.org_id, token_version=user.token_version
        )
        return TokenResponse(access_token=access), refresh

    async def refresh(self, refresh_token: str) -> tuple[TokenResponse, str]:
        from app.utils.tokens import TokenError, decode_refresh_token

        try:
            payload = decode_refresh_token(refresh_token)
        except TokenError as exc:
            raise InvalidCredentials("Invalid refresh token") from exc

        user_id = uuid.UUID(payload["sub"])
        org_id = uuid.UUID(payload["org_id"])
        # The refresh token's org claim scopes the lookup (F60) — under RLS a mismatched
        # claim reads nothing → InvalidCredentials. The explicit org check below keeps
        # the same guarantee on RLS-bypassing connections (dev/test superuser).
        async with db_mod.tenant_session(org_id) as session:
            auth_repo = AuthRepository(session)
            user = await auth_repo.get_user_by_id(user_id)

        if (
            user is None
            or user.org_id != org_id
            or not user.password_hash
            or not user.is_active
            or user.token_version != payload.get("tv")
        ):
            raise InvalidCredentials("Invalid refresh token")

        access = issue_access_token(
            user_id=user.id,
            org_id=user.org_id,
            role=user.role,
            email=user.email,
            token_version=user.token_version,
        )
        new_refresh = issue_refresh_token(
            user_id=user.id, org_id=user.org_id, token_version=user.token_version
        )
        return TokenResponse(access_token=access), new_refresh

    async def invite(self, ctx: TenantContext, req: InviteRequest) -> InviteOut:
        """Creates the user with NO password yet (``password_hash=None`` — ``login()``'s
        existing ``if not user.password_hash`` branch already rejects login for them)
        plus a one-time, hashed invite token. The RAW token is returned exactly once
        here, in ``InviteOut.invite_token`` — never persisted, never logged. The
        inviting admin builds a link from it and sends it to the invitee by whatever
        channel they like; the invitee sets their OWN password via ``accept_invite``."""
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can invite users")
        if req.role not in ROLES or req.role == ROLE_OWNER:
            raise Forbidden("Invalid invite role")

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        async with db_mod.tenant_session(ctx.org_id) as session:
            user_repo = UserRepository(session, ctx)
            if await user_repo.get_by_email(req.email):
                raise EmailTaken("Email already in this organization")

            auth_repo = AuthRepository(session)
            user = await auth_repo.create_user(
                org_id=ctx.org_id,
                email=req.email,
                password_hash=None,
                role=req.role,
            )

            invite_repo = InviteTokenRepository(session, ctx)
            await invite_repo.create(
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(days=_INVITE_TOKEN_TTL_DAYS),
                token_hash=token_hash,
            )

        return InviteOut(
            user=UserOut.model_validate(user), org_id=ctx.org_id, invite_token=raw_token
        )

    async def accept_invite(self, req: AcceptInviteRequest) -> tuple[TokenResponse, str]:
        """The invitee sets their OWN password. ``org_id`` is a routing identifier
        carried by the link (not the secret — the token is), so this can use the
        ordinary ``tenant_session`` rather than a second pre-tenant bootstrap system.
        On success the invitee is logged straight in, mirroring ``signup``'s tail."""
        token_hash = hashlib.sha256(req.token.encode()).hexdigest()

        async with db_mod.tenant_session(req.org_id) as session:
            ctx = TenantContext(org_id=req.org_id)
            invite_repo = InviteTokenRepository(session, ctx)
            invite = await invite_repo.get_valid_by_hash(token_hash)
            if invite is None:
                raise InvalidInviteToken("Invalid or expired invite link")

            user_repo = UserRepository(session, ctx)
            user = await user_repo.get_by_id(invite.user_id)
            if user is None:
                # Defensive/unreachable given the FK — never leak which failure mode.
                raise InvalidInviteToken("Invalid or expired invite link")

            updated = await user_repo.update_password(user, hash_password(req.password))
            await invite_repo.mark_used(invite)

        access = issue_access_token(
            user_id=updated.id,
            org_id=updated.org_id,
            role=updated.role,
            email=updated.email,
            token_version=updated.token_version,
        )
        refresh = issue_refresh_token(
            user_id=updated.id, org_id=updated.org_id, token_version=updated.token_version
        )
        return TokenResponse(access_token=access), refresh

    async def list_org_users(self, ctx: TenantContext) -> list[UserOut]:
        async with db_mod.tenant_session(ctx.org_id) as session:
            user_repo = UserRepository(session, ctx)
            users = await user_repo.list()
        return [UserOut.model_validate(u) for u in users]

    async def get_user(self, ctx: TenantContext, user_id: uuid.UUID) -> UserOut:
        """Org-scoped existence lookup for OTHER modules (module-boundary rule). First
        caller: ``access_roles.service``, validating a member exists in this org before
        assigning them an Access Role."""
        async with db_mod.tenant_session(ctx.org_id) as session:
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

        async with db_mod.tenant_session(ctx.org_id) as session:
            user_repo = UserRepository(session, ctx)
            target = await user_repo.get_by_id(target_user_id)
            if target is None:
                raise TargetUserNotFound("User not found")
            if target.role == ROLE_OWNER:
                raise Forbidden("Cannot change an owner's role")

            updated = await user_repo.update_role(target, new_role)

        return UserOut.model_validate(updated)

    async def set_member_active(
        self, ctx: TenantContext, target_user_id: uuid.UUID, is_active: bool
    ) -> UserOut:
        """Deactivate = the org's "remove a member" action. Soft, reversible: the row
        stays (preserving FK-referenced history in chat/knowledge tables) but is blocked
        at every auth boundary the instant it's deactivated (see middleware/deps.py and
        AuthService.refresh — both re-check ``is_active`` on every request)."""
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can change member status")
        if target_user_id == ctx.user_id:
            raise Forbidden("Cannot change your own active status")

        async with db_mod.tenant_session(ctx.org_id) as session:
            user_repo = UserRepository(session, ctx)
            target = await user_repo.get_by_id(target_user_id)
            if target is None:
                raise TargetUserNotFound("User not found")
            if target.role == ROLE_OWNER:
                raise Forbidden("Cannot change an owner's active status")

            updated = await user_repo.set_active(target, is_active)

        return UserOut.model_validate(updated)

    async def change_password(
        self, ctx: TenantContext, current_password: str, new_password: str
    ) -> tuple[TokenResponse, str]:
        """Self-service only (``ctx.user_id`` — no admin-reset path exists). Rotates
        ``token_version``, which invalidates every other session; returns a fresh token
        pair so the session making the change keeps working."""
        async with db_mod.tenant_session(ctx.org_id) as session:
            user_repo = UserRepository(session, ctx)
            user = await user_repo.get_by_id(ctx.user_id)  # type: ignore[arg-type]
            if user is None:
                raise UserNotFound("User not found")
            if not user.password_hash or not verify_password(current_password, user.password_hash):
                raise InvalidCredentials("Current password is incorrect")

            updated = await user_repo.update_password(user, hash_password(new_password))

        access = issue_access_token(
            user_id=updated.id,
            org_id=updated.org_id,
            role=updated.role,
            email=updated.email,
            token_version=updated.token_version,
        )
        refresh = issue_refresh_token(
            user_id=updated.id, org_id=updated.org_id, token_version=updated.token_version
        )
        return TokenResponse(access_token=access), refresh

    async def get_org(self, ctx: TenantContext) -> OrganizationOut:
        async with db_mod.tenant_session(ctx.org_id) as session:
            org = await OrganizationRepository(session).get(ctx.org_id)
        return OrganizationOut.model_validate(org)

    async def rename_org(self, ctx: TenantContext, new_name: str) -> OrganizationOut:
        if ctx.role not in ADMIN_ROLES:
            raise Forbidden("Only owners and admins can rename the organization")

        async with db_mod.tenant_session(ctx.org_id) as session:
            org_repo = OrganizationRepository(session)
            org = await org_repo.get(ctx.org_id)
            updated = await org_repo.update_name(org, new_name)

        return OrganizationOut.model_validate(updated)


auth_service = AuthService()
