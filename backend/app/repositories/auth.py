"""Identity repositories — all SQL for orgs/users lives here (codestandards "Layering").

F02 ships the tenant-scoped ``UserRepository`` for app-level isolation. F10 adds
``OrganizationRepository`` (tenancy root — keys on ``id``, not ``org_id``) and
``AuthRepository`` for signup/login lookups that must span orgs.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Organization, User
from app.platform.repository import BaseRepository


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
