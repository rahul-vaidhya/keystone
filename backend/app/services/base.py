"""The base repository — the single place the always-on app-level tenant filter lives.

Every module's repository extends ``BaseRepository`` and builds its queries through
``_scoped(...)``, which appends ``WHERE <model>.org_id = :org`` from the ``TenantContext``.
This filter is applied on EVERY query, ALWAYS, independent of ``RLS_ENABLED`` — it is the
MVP isolation guarantee; Postgres RLS (Phase 6 / F60) is only the backstop. See
codestandards.md "Tenancy" and architecture.md "Boundaries".

``org_id`` comes from the authenticated context / job payload, never from a request body.
The tenancy root (``organizations``) has no ``org_id`` column — it keys on ``id`` — so its
repository (Phase 1) scopes itself rather than using this base.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.context import TenantContext


class BaseRepository[ModelT]:
    """Base for tenant-scoped repositories. Subclasses set ``model`` to a mapped class that
    carries an ``org_id`` column."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession, ctx: TenantContext) -> None:
        self._db = session
        self._ctx = ctx

    def _scoped(self, stmt: Select | None = None) -> Select:
        """Return ``stmt`` (default: ``select(self.model)``) narrowed to the caller's org.

        This is the choke point: no tenant-scoped query leaves a repository without the
        ``org_id`` predicate, so a missing filter is impossible by construction.
        """
        if stmt is None:
            stmt = select(self.model)
        return stmt.where(self.model.org_id == self._ctx.org_id)
