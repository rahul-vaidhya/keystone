"""Notebooks use cases: CRUD, document association, per-person sharing, and the P1
Notebook Overview artifact.

This package is a structural split of what used to be one flat ``knowledge.py`` (332
lines mixing notebook CRUD, document attach/detach, and sharing — already past the
package-layout convention's ~200-line trigger before this feature, but left flat by an
earlier session's judgment call that it was still "one cohesive concern"). Adding the
Notebook Overview artifact (P1 roadmap) tipped it: LLM-orchestrated map-reduce
generation + its own table/repository is a genuinely independent responsibility from
plain CRUD/sharing SQL, not just more of the same. Split by subdomain (same convention
as ``app.services.documents``'s ``folders.py``/``tags.py``/``documents.py``, each
module owning its own repository classes) rather than by axis (like
``app.services.chat``'s ``repository.py``/``service.py``): ``notebooks.py`` (CRUD +
attach/detach + sharing, the original file's content verbatim, zero logic change) and
``overview.py`` (new). ``KnowledgeService`` here is pure delegation, so
``knowledge_service.create_notebook(...)`` etc. resolve exactly as before.

``overview.py`` imports ``notebooks.py``'s ``fetch_visible`` at module level;
``notebooks.py``'s ``attach_document``/``detach_document`` import ``overview.py``'s
``mark_stale`` via a DEFERRED (function-local) import to break what would otherwise be
a circular import between the two sibling modules.
"""

from __future__ import annotations

import uuid

from app.middleware.context import TenantContext
from app.models.documents import DocumentOut
from app.models.knowledge import (
    NotebookCreate,
    NotebookOut,
    NotebookOverviewOut,
    NotebookShareCreate,
    NotebookShareOut,
    NotebookUpdate,
)
from app.services.knowledge import notebooks as _notebooks
from app.services.knowledge import overview as _overview
from app.services.knowledge.exceptions import (
    KnowledgeError as KnowledgeError,
)
from app.services.knowledge.exceptions import (
    NotebookAccessDenied as NotebookAccessDenied,
)
from app.services.knowledge.exceptions import (
    NotebookNotFound as NotebookNotFound,
)
from app.services.knowledge.exceptions import (
    OverviewNotFound as OverviewNotFound,
)
from app.services.knowledge.exceptions import (
    OverviewUnavailable as OverviewUnavailable,
)
from app.services.seams import LLM


class KnowledgeService:
    async def create_notebook(self, ctx: TenantContext, req: NotebookCreate) -> NotebookOut:
        return await _notebooks.create_notebook(ctx, req)

    async def list_notebooks(self, ctx: TenantContext) -> list[NotebookOut]:
        return await _notebooks.list_notebooks(ctx)

    async def get_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> NotebookOut:
        return await _notebooks.get_notebook(ctx, notebook_id)

    async def update_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, req: NotebookUpdate
    ) -> NotebookOut:
        return await _notebooks.update_notebook(ctx, notebook_id, req)

    async def delete_notebook(self, ctx: TenantContext, notebook_id: uuid.UUID) -> None:
        return await _notebooks.delete_notebook(ctx, notebook_id)

    async def attach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        return await _notebooks.attach_document(ctx, notebook_id, document_id)

    async def detach_document(
        self, ctx: TenantContext, notebook_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        return await _notebooks.detach_document(ctx, notebook_id, document_id)

    async def list_notebook_documents(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[DocumentOut]:
        return await _notebooks.list_notebook_documents(ctx, notebook_id)

    async def share_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, req: NotebookShareCreate
    ) -> None:
        return await _notebooks.share_notebook(ctx, notebook_id, req)

    async def unshare_notebook(
        self, ctx: TenantContext, notebook_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        return await _notebooks.unshare_notebook(ctx, notebook_id, user_id)

    async def list_shares(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[NotebookShareOut]:
        return await _notebooks.list_shares(ctx, notebook_id)

    async def generate_overview(
        self, ctx: TenantContext, notebook_id: uuid.UUID, *, llm: LLM
    ) -> NotebookOverviewOut:
        return await _overview.generate_overview(ctx, notebook_id, llm=llm)

    async def get_overview(self, ctx: TenantContext, notebook_id: uuid.UUID) -> NotebookOverviewOut:
        return await _overview.get_overview(ctx, notebook_id)


knowledge_service = KnowledgeService()
