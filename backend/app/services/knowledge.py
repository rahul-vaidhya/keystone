"""Notebook use cases.

Document existence/ownership checks go through ``documents.service`` — never
``documents.repository`` or ``documents.models`` directly (module-boundary rule:
cross-module calls go through a service, never another module's repository or tables).
"""

from __future__ import annotations

import uuid

from app.exceptions.knowledge import NotebookNotFound
from app.platform import db as db_mod
from app.platform.context import TenantContext
from app.repositories.knowledge import NotebookDocumentRepository, NotebookRepository
from app.schemas.documents import DocumentOut
from app.schemas.knowledge import NotebookCreate, NotebookOut, NotebookUpdate
from app.services.documents import documents_service


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
