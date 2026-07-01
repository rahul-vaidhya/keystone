"""Documents HTTP routes (folders, tags, document tagging, document listing) — thin; all
logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.controllers.deps import get_ctx
from app.platform.context import TenantContext
from app.platform.queue import JobQueue, get_job_queue
from app.platform.storage import ObjectStore, get_object_store
from app.schemas.documents import (
    DocumentOut,
    FolderCreate,
    FolderMove,
    FolderOut,
    FolderRename,
    TagCreate,
    TagOut,
)
from app.services.documents import documents_service
from app.services.documents.folders import DeleteMode
from app.services.ingestion import ingestion_service

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
    folder_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    mode: DeleteMode = "block",
) -> None:
    await documents_service.delete_folder(ctx, folder_id, mode=mode)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: uuid.UUID,
    req: FolderRename,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> FolderOut:
    return await documents_service.rename_folder(ctx, folder_id, req.name)


@router.post("/folders/{folder_id}/move", response_model=FolderOut)
async def move_folder(
    folder_id: uuid.UUID,
    req: FolderMove,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> FolderOut:
    return await documents_service.move_folder(ctx, folder_id, req.parent_id)


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


@router.post("/upload")
async def upload_document(
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue)],
    file: UploadFile,
    folder_id: Annotated[uuid.UUID | None, Form()] = None,
) -> JSONResponse:
    data = await file.read()
    doc, created = await documents_service.upload_document(
        ctx,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        folder_id=folder_id,
        object_store=object_store,
    )
    # F24: only a genuine new upload starts the pipeline — a dedupe hit (created=False)
    # returns an already-ingested (or already in-flight) document, which must not be
    # re-dispatched. The upload's own transaction has already committed by this point
    # (documents_service.upload_document commits internally) — never enqueue before that,
    # or a rolled-back upload could leave a job pointing at a row that doesn't exist.
    if created:
        await ingestion_service.enqueue_pipeline(ctx, doc.id, job_queue=job_queue)
    return JSONResponse(
        content=doc.model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
