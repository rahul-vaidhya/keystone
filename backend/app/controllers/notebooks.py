"""Notebook HTTP routes — thin; all logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.controllers.deps import get_ctx
from app.platform.context import TenantContext
from app.schemas.documents import DocumentOut
from app.schemas.knowledge import NotebookCreate, NotebookOut, NotebookUpdate
from app.services.knowledge import knowledge_service

router = APIRouter(prefix="/notebooks", tags=["notebooks"])


@router.post("", response_model=NotebookOut, status_code=status.HTTP_201_CREATED)
async def create_notebook(
    req: NotebookCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> NotebookOut:
    return await knowledge_service.create_notebook(ctx, req)


@router.get("", response_model=list[NotebookOut])
async def list_notebooks(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[NotebookOut]:
    return await knowledge_service.list_notebooks(ctx)


@router.get("/{notebook_id}", response_model=NotebookOut)
async def get_notebook(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> NotebookOut:
    return await knowledge_service.get_notebook(ctx, notebook_id)


@router.patch("/{notebook_id}", response_model=NotebookOut)
async def update_notebook(
    notebook_id: uuid.UUID,
    req: NotebookUpdate,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> NotebookOut:
    return await knowledge_service.update_notebook(ctx, notebook_id, req)


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> None:
    await knowledge_service.delete_notebook(ctx, notebook_id)


@router.post("/{notebook_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def attach_document(
    notebook_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.attach_document(ctx, notebook_id, document_id)


@router.delete("/{notebook_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_document(
    notebook_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await knowledge_service.detach_document(ctx, notebook_id, document_id)


@router.get("/{notebook_id}/documents", response_model=list[DocumentOut])
async def list_notebook_documents(
    notebook_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> list[DocumentOut]:
    return await knowledge_service.list_notebook_documents(ctx, notebook_id)
