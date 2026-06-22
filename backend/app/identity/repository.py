"""Identity repositories — all SQL for orgs/users lives here (codestandards "Layering").

F02 ships only the tenant-scoped ``UserRepository`` needed to prove app-level isolation; it
extends ``BaseRepository`` so its reads are always narrowed to the caller's ``org_id``. Auth
behaviour (signup, login, invites, role enforcement) is Phase 1 / F10 and lands here too.
"""

from __future__ import annotations

from app.identity.models import User
from app.platform.repository import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def list(self) -> list[User]:
        """All users in the caller's org (app-level ``org_id`` filter applied by base)."""
        result = await self._db.scalars(self._scoped())
        return list(result)
