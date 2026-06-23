"""Knowledge (notebooks) repositories — all SQL for notebooks and notebook-document
associations lives here (codestandards "Layering")."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.knowledge.models import Notebook, NotebookDocument
from app.platform.repository import BaseRepository


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
