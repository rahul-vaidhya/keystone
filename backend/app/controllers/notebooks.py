"""Notebook HTTP handlers — thin; all logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx
from app.models.documents import DocumentOut
from app.models.knowledge import (
    NotebookCreate,
    NotebookOut,
    NotebookOverviewOut,
    NotebookShareCreate,
    NotebookShareOut,
    NotebookUpdate,
)
from app.services.knowledge import knowledge_service
from app.services.seams import LLM, get_llm


async def create_notebook(
    req: NotebookCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> NotebookOut:
    return await knowledge_service.create_notebook(ctx, req)


async def list_notebooks(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[NotebookOut]:
    return await knowledge_service.list_notebooks(ctx)


async def get_notebook(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> NotebookOut:
    return await knowledge_service.get_notebook(ctx, notebook_id)


async def update_notebook(
    notebook_id: uuid.UUID,
    req: NotebookUpdate,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> NotebookOut:
    return await knowledge_service.update_notebook(ctx, notebook_id, req)


async def delete_notebook(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> None:
    await knowledge_service.delete_notebook(ctx, notebook_id)


async def attach_document(
    notebook_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.attach_document(ctx, notebook_id, document_id)


async def detach_document(
    notebook_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.detach_document(ctx, notebook_id, document_id)


async def list_notebook_documents(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> list[DocumentOut]:
    return await knowledge_service.list_notebook_documents(ctx, notebook_id)


async def list_shares(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> list[NotebookShareOut]:
    return await knowledge_service.list_shares(ctx, notebook_id)


async def share_notebook(
    notebook_id: uuid.UUID,
    req: NotebookShareCreate,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.share_notebook(ctx, notebook_id, req)


async def unshare_notebook(
    notebook_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.unshare_notebook(ctx, notebook_id, user_id)


async def generate_overview(
    notebook_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> NotebookOverviewOut:
    """Generate/regenerate the notebook's Overview. Any notebook member may call this —
    same access level chat already uses for this notebook (the real gate is inside the
    service, via ``knowledge_service.get_notebook``'s ``fetch_visible``)."""
    return await knowledge_service.generate_overview(ctx, notebook_id, llm=llm)


async def get_overview(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> NotebookOverviewOut:
    """Fetch the cached Overview. Same access level as ``generate_overview``."""
    return await knowledge_service.get_overview(ctx, notebook_id)
