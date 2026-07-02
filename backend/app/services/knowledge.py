"""Notebook use cases.

Document existence/ownership checks go through ``documents.service`` — never
``documents.repository`` or ``documents.models`` directly (module-boundary rule:
cross-module calls go through a service, never another module's repository or tables).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut
from app.models.knowledge import (
    Notebook,
    NotebookCreate,
    NotebookDocument,
    NotebookOut,
    NotebookUpdate,
)
from app.services.base import BaseRepository
from app.services.documents import documents_service


# ---- exceptions ----
class KnowledgeError(Exception):
    """Base notebooks failure."""


class NotebookNotFound(KnowledgeError):
    pass


# ---- repository ----
class NotebookRepository(BaseRepository[Notebook]):
    model = Notebook

    async def list(self) -> list[Notebook]:
        stmt = self._scoped().order_by(Notebook.created_at)
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


# ---- service ----


class KnowledgeService:
    async def create_notebook(self, ctx: TenantContext, req: NotebookCreate) -> NotebookOut:
        async with db_mod.sessionmaker() as session, session.begin():
            notebook = await NotebookRepository(session, ctx).create(
                name=req.name, description=req.description, created_by=ctx.user_id
            )
        return NotebookOut.model_validate(notebook)

    async def list_notebooks(self, ctx: TenantContext) -> list[NotebookOut]:
        async with db_mod.sessionmaker() as session:
            notebooks = await NotebookRepository(session, ctx).list()
        return [NotebookOut.model_validate(n) for n in notebooks]

    async def get_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> NotebookOut:
        async with db_mod.sessionmaker() as session:
            notebook = await NotebookRepository(session, ctx).get_by_id(notebook_id)
        if notebook is None:
            raise NotebookNotFound("Notebook not found")
        return NotebookOut.model_validate(notebook)

    async def update_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, req: NotebookUpdate
    ) -> NotebookOut:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = NotebookRepository(session, ctx)
            notebook = await repo.get_by_id(notebook_id)
            if notebook is None:
                raise NotebookNotFound("Notebook not found")
            await repo.update(notebook, name=req.name, description=req.description)
            out = NotebookOut.model_validate(notebook)
        return out

    async def delete_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            repo = NotebookRepository(session, ctx)
            notebook = await repo.get_by_id(notebook_id)
            if notebook is None:
                raise NotebookNotFound("Notebook not found")
            await repo.delete(notebook)

    async def attach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Idempotent. Validates the notebook exists and the document belongs to the same
        org (via ``documents_service.get_document``, which raises ``DocumentNotFound`` if not
        — re-raised as-is since both modules' "not found" exceptions map to 404 the same way)
        before attaching."""
        async with db_mod.sessionmaker() as session, session.begin():
            notebook = await NotebookRepository(session, ctx).get_by_id(notebook_id)
            if notebook is None:
                raise NotebookNotFound("Notebook not found")
            await documents_service.get_document(ctx, document_id)
            await NotebookDocumentRepository(session, ctx).attach(notebook_id, document_id)

    async def detach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        async with db_mod.sessionmaker() as session, session.begin():
            await NotebookDocumentRepository(session, ctx).detach(notebook_id, document_id)

    async def list_notebook_documents(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[DocumentOut]:
        async with db_mod.sessionmaker() as session:
            notebook = await NotebookRepository(session, ctx).get_by_id(notebook_id)
            if notebook is None:
                raise NotebookNotFound("Notebook not found")
            document_ids = await NotebookDocumentRepository(session, ctx).list_document_ids(
                notebook_id
            )
        return await documents_service.list_by_ids(ctx, document_ids)


knowledge_service = KnowledgeService()
