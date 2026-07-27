"""Notebook use cases.

Document existence/ownership checks go through ``documents.service`` — never
``documents.repository`` or ``documents.models`` directly (module-boundary rule:
cross-module calls go through a service, never another module's repository or tables).

Notebook visibility (docs/notebook-privacy-plan): a notebook is private to its
``created_by`` user by default. Other org members — including owner/admin, deliberately
with NO bypass here — see it only once the creator shares it via ``NotebookShare``.
Shared users get view+chat access only; every mutating operation (rename, delete,
attach/detach documents, managing shares itself) stays creator-only. The check is
skipped entirely when ``ctx.user_id is None`` — the anonymous public embed-widget path
(``services/embed.py``) never carries a real user and must stay unaffected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut
from app.models.knowledge import (
    Notebook,
    NotebookCreate,
    NotebookDocument,
    NotebookOut,
    NotebookShare,
    NotebookShareCreate,
    NotebookShareOut,
    NotebookUpdate,
)
from app.services.base import BaseRepository
from app.services.documents import documents_service


# ---- exceptions ----
class KnowledgeError(Exception):
    """Base notebooks failure."""


class NotebookNotFound(KnowledgeError):
    pass


class NotebookAccessDenied(KnowledgeError):
    """Raised when an authenticated user (``ctx.user_id`` is set) who is neither the
    notebook's creator nor a share recipient (for view-level checks — manage-level
    checks require creator regardless of shares) requests it. Maps to 403, not 404 —
    unlike ``NotebookNotFound``, this deliberately confirms the notebook exists."""


# ---- repository ----
class NotebookRepository(BaseRepository[Notebook]):
    model = Notebook

    async def list_visible(self, user_id: uuid.UUID) -> list[Notebook]:
        """Notebooks ``user_id`` created OR was explicitly shared into — never every
        org notebook. No owner/admin bypass (see module docstring)."""
        shared_ids = select(NotebookShare.notebook_id).where(
            NotebookShare.org_id == self._ctx.org_id, NotebookShare.user_id == user_id
        )
        stmt = (
            self._scoped()
            .where((Notebook.created_by == user_id) | (Notebook.id.in_(shared_ids)))
            .order_by(Notebook.created_at)
        )
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, notebook_id: uuid.UUID) -> Notebook | None:
        stmt = self._scoped().where(Notebook.id == notebook_id)
        return await self._db.scalar(stmt)

    async def create(
        self, *, name: str, description: str | None, created_by: uuid.UUID | None
    ) -> Notebook:
        notebook = Notebook(
            org_id=self._ctx.org_id, name=name, description=description, created_by=created_by
        )
        self._db.add(notebook)
        await self._db.flush()
        return notebook

    async def update(
        self, notebook: Notebook, *, name: str | None, description: str | None
    ) -> Notebook:
        """Sets ``updated_at`` explicitly rather than relying on the column's server-side
        ``onupdate`` — the latter only populates the Python attribute on a post-flush refresh,
        which requires IO the caller's sync ``model_validate`` can't trigger inside an async
        session (``MissingGreenlet``)."""
        if name is not None:
            notebook.name = name
        if description is not None:
            notebook.description = description
        notebook.updated_at = datetime.now(UTC)
        await self._db.flush()
        return notebook

    async def delete(self, notebook: Notebook) -> None:
        await self._db.delete(notebook)


class NotebookDocumentRepository(BaseRepository[NotebookDocument]):
    model = NotebookDocument

    async def attach(self, notebook_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Idempotent: attaching an already-attached document is a no-op."""
        stmt = (
            pg_insert(NotebookDocument)
            .values(org_id=self._ctx.org_id, knowledge_base_id=notebook_id, document_id=document_id)
            .on_conflict_do_nothing(index_elements=["knowledge_base_id", "document_id"])
        )
        await self._db.execute(stmt)

    async def detach(self, notebook_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Idempotent: detaching a document that isn't attached is a no-op."""
        stmt = self._scoped().where(
            NotebookDocument.knowledge_base_id == notebook_id,
            NotebookDocument.document_id == document_id,
        )
        link = await self._db.scalar(stmt)
        if link is not None:
            await self._db.delete(link)

    async def list_document_ids(self, notebook_id: uuid.UUID) -> list[uuid.UUID]:
        """Returns only the ids of documents attached to this notebook — knowledge owns
        ``knowledge_base_documents``, not ``documents``, so the document rows themselves are
        fetched by the caller through ``documents.service`` (module-boundary rule: cross-module
        calls go through a service, never another module's repository or tables)."""
        stmt = (
            self._scoped()
            .where(NotebookDocument.knowledge_base_id == notebook_id)
            .order_by(NotebookDocument.added_at)
        )
        rows = await self._db.scalars(stmt)
        return [row.document_id for row in rows]


class NotebookShareRepository(BaseRepository[NotebookShare]):
    model = NotebookShare

    async def exists(self, notebook_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        stmt = self._scoped().where(
            NotebookShare.notebook_id == notebook_id, NotebookShare.user_id == user_id
        )
        return await self._db.scalar(stmt) is not None

    async def create(
        self, notebook_id: uuid.UUID, user_id: uuid.UUID, shared_by: uuid.UUID | None
    ) -> None:
        """Idempotent: re-sharing with someone already shared is a no-op."""
        stmt = (
            pg_insert(NotebookShare)
            .values(
                org_id=self._ctx.org_id,
                notebook_id=notebook_id,
                user_id=user_id,
                shared_by=shared_by,
            )
            .on_conflict_do_nothing(index_elements=["notebook_id", "user_id"])
        )
        await self._db.execute(stmt)

    async def delete(self, notebook_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Idempotent: unsharing from someone not currently shared is a no-op."""
        stmt = self._scoped().where(
            NotebookShare.notebook_id == notebook_id, NotebookShare.user_id == user_id
        )
        share = await self._db.scalar(stmt)
        if share is not None:
            await self._db.delete(share)

    async def list_for_notebook(self, notebook_id: uuid.UUID) -> list[NotebookShare]:
        stmt = (
            self._scoped()
            .where(NotebookShare.notebook_id == notebook_id)
            .order_by(NotebookShare.created_at)
        )
        return list(await self._db.scalars(stmt))


# ---- service ----


async def _fetch_visible(
    session: AsyncSession, ctx: TenantContext, notebook_id: uuid.UUID
) -> Notebook:
    """View-level fetch: creator, a share recipient, or an anonymous (widget) ctx may
    read the notebook. Everyone else gets ``NotebookAccessDenied``. ``ctx.user_id is
    None`` (the public embed path) always passes — that path proves consent a
    different way (an admin deliberately created a public widget for the notebook)."""
    notebook = await NotebookRepository(session, ctx).get_by_id(notebook_id)
    if notebook is None:
        raise NotebookNotFound("Notebook not found")
    if ctx.user_id is None or notebook.created_by == ctx.user_id:
        return notebook
    if await NotebookShareRepository(session, ctx).exists(notebook_id, ctx.user_id):
        return notebook
    raise NotebookAccessDenied("You do not have access to this notebook")


async def _fetch_manageable(
    session: AsyncSession, ctx: TenantContext, notebook_id: uuid.UUID
) -> Notebook:
    """Manage-level fetch: only the creator (or an anonymous ctx, for symmetry with
    ``_fetch_visible`` — no anonymous caller actually mutates a notebook today) may
    rename/delete it, attach/detach documents, or manage its shares. A share recipient
    is visible but never manageable — enforces the "view + chat only" contract."""
    notebook = await NotebookRepository(session, ctx).get_by_id(notebook_id)
    if notebook is None:
        raise NotebookNotFound("Notebook not found")
    if ctx.user_id is None or notebook.created_by == ctx.user_id:
        return notebook
    raise NotebookAccessDenied("Only the notebook's creator can do this")


class KnowledgeService:
    async def create_notebook(self, ctx: TenantContext, req: NotebookCreate) -> NotebookOut:
        async with db_mod.tenant_session(ctx.org_id) as session:
            notebook = await NotebookRepository(session, ctx).create(
                name=req.name, description=req.description, created_by=ctx.user_id
            )
        return NotebookOut.model_validate(notebook)

    async def list_notebooks(self, ctx: TenantContext) -> list[NotebookOut]:
        async with db_mod.tenant_session(ctx.org_id) as session:
            if ctx.user_id is None:
                notebooks: list[Notebook] = []
            else:
                notebooks = await NotebookRepository(session, ctx).list_visible(ctx.user_id)
        return [NotebookOut.model_validate(n) for n in notebooks]

    async def get_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> NotebookOut:
        async with db_mod.tenant_session(ctx.org_id) as session:
            notebook = await _fetch_visible(session, ctx, notebook_id)
        return NotebookOut.model_validate(notebook)

    async def update_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, req: NotebookUpdate
    ) -> NotebookOut:
        async with db_mod.tenant_session(ctx.org_id) as session:
            repo = NotebookRepository(session, ctx)
            notebook = await _fetch_manageable(session, ctx, notebook_id)
            await repo.update(notebook, name=req.name, description=req.description)
            out = NotebookOut.model_validate(notebook)
        return out

    async def delete_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> None:
        async with db_mod.tenant_session(ctx.org_id) as session:
            notebook = await _fetch_manageable(session, ctx, notebook_id)
            await NotebookRepository(session, ctx).delete(notebook)

    async def attach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Idempotent. Validates the notebook is manageable by ``ctx`` and the document
        belongs to the same org (via ``documents_service.get_document``, which raises
        ``DocumentNotFound`` if not — re-raised as-is since both modules' "not found"
        exceptions map to 404 the same way) before attaching."""
        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_manageable(session, ctx, notebook_id)
            await documents_service.get_document(ctx, document_id)
            await NotebookDocumentRepository(session, ctx).attach(notebook_id, document_id)

    async def detach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_manageable(session, ctx, notebook_id)
            await NotebookDocumentRepository(session, ctx).detach(notebook_id, document_id)

    async def list_notebook_documents(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[DocumentOut]:
        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_visible(session, ctx, notebook_id)
            document_ids = await NotebookDocumentRepository(session, ctx).list_document_ids(
                notebook_id
            )
        return await documents_service.list_by_ids(ctx, document_ids)

    async def share_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, req: NotebookShareCreate
    ) -> None:
        """Creator-only. Validates the target user exists in this org via
        ``auth_service.get_users_by_ids`` (module-boundary rule — never a direct
        ``users`` table read from this module); local import mirrors
        ``documents.service``'s established auth-import precedent (avoids a circular
        import at module load time)."""
        from app.services.auth import auth_service

        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_manageable(session, ctx, notebook_id)
            emails = await auth_service.get_users_by_ids(ctx, [req.user_id])
            if req.user_id not in emails:
                raise NotebookNotFound("User not found")
            await NotebookShareRepository(session, ctx).create(
                notebook_id, req.user_id, shared_by=ctx.user_id
            )

    async def unshare_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_manageable(session, ctx, notebook_id)
            await NotebookShareRepository(session, ctx).delete(notebook_id, user_id)

    async def list_shares(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[NotebookShareOut]:
        from app.services.auth import auth_service

        async with db_mod.tenant_session(ctx.org_id) as session:
            await _fetch_manageable(session, ctx, notebook_id)
            shares = await NotebookShareRepository(session, ctx).list_for_notebook(notebook_id)
        emails = await auth_service.get_users_by_ids(ctx, [s.user_id for s in shares])
        return [
            NotebookShareOut(
                user_id=s.user_id,
                email=emails.get(s.user_id, "(removed user)"),
                created_at=s.created_at,
            )
            for s in shares
        ]


knowledge_service = KnowledgeService()
