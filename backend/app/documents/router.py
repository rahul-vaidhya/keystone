"""Documents HTTP routes (folders, tags, document tagging, document listing) — thin; all
logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.documents.schemas import DocumentOut, FolderCreate, FolderOut, TagCreate, TagOut
from app.documents.service import documents_service
from app.identity.deps import get_ctx
from app.platform.context import TenantContext

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    req: FolderCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> FolderOut:
    return await documents_service.create_folder(ctx, req)


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[FolderOut]:
    return await documents_service.list_folders(ctx)


@router.get("/folders/{folder_id}", response_model=FolderOut)
async def get_folder(
    folder_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> FolderOut:
    return await documents_service.get_folder(ctx, folder_id)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> None:
    await documents_service.delete_folder(ctx, folder_id)


@router.post("/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(req: TagCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]) -> TagOut:
    return await documents_service.create_tag(ctx, req)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[TagOut]:
    return await documents_service.list_tags(ctx)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]) -> None:
    await documents_service.delete_tag(ctx, tag_id)


@router.post("/{document_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def tag_document(
    document_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await documents_service.tag_document(ctx, document_id, tag_id)


@router.delete("/{document_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def untag_document(
    document_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await documents_service.untag_document(ctx, document_id, tag_id)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    folder_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
) -> list[DocumentOut]:
    return await documents_service.list_documents(ctx, folder_id=folder_id, tag_id=tag_id)
