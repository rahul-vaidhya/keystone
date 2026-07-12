"""Documents HTTP handlers (folders, tags, document tagging, document listing) — thin; all
logic in service."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx, require_admin
from app.models.documents import (
    DocumentMove,
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
from app.services.queue import JobQueue, get_job_queue
from app.services.storage import ObjectStore, get_object_store


async def create_folder(
    req: FolderCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> FolderOut:
    return await documents_service.create_folder(ctx, req)


async def list_folders(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[FolderOut]:
    return await documents_service.list_folders(ctx)


async def get_folder(
    folder_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]
) -> FolderOut:
    return await documents_service.get_folder(ctx, folder_id)


async def delete_folder(
    folder_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    mode: DeleteMode = "block",
) -> None:
    await documents_service.delete_folder(ctx, folder_id, mode=mode)


async def rename_folder(
    folder_id: uuid.UUID,
    req: FolderRename,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> FolderOut:
    return await documents_service.rename_folder(ctx, folder_id, req.name)


async def move_folder(
    folder_id: uuid.UUID,
    req: FolderMove,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> FolderOut:
    return await documents_service.move_folder(ctx, folder_id, req.parent_id)


async def tag_folder(
    folder_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    # Admin/owner-only: a folder tag cascades to its whole subtree (docs/
    # access-roles-dnd-plan.md), unlike document tagging below (open to any member).
    await documents_service.tag_folder(ctx, folder_id, tag_id)


async def untag_folder(
    folder_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await documents_service.untag_folder(ctx, folder_id, tag_id)


async def create_tag(req: TagCreate, ctx: Annotated[TenantContext, Depends(get_ctx)]) -> TagOut:
    return await documents_service.create_tag(ctx, req)


async def list_tags(ctx: Annotated[TenantContext, Depends(get_ctx)]) -> list[TagOut]:
    return await documents_service.list_tags(ctx)


async def delete_tag(tag_id: uuid.UUID, ctx: Annotated[TenantContext, Depends(get_ctx)]) -> None:
    await documents_service.delete_tag(ctx, tag_id)


async def tag_document(
    document_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await documents_service.tag_document(ctx, document_id, tag_id)


async def untag_document(
    document_id: uuid.UUID,
    tag_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> None:
    await documents_service.untag_document(ctx, document_id, tag_id)


async def list_documents(
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    folder_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
) -> list[DocumentOut]:
    return await documents_service.list_documents(ctx, folder_id=folder_id, tag_id=tag_id)


async def delete_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> None:
    await documents_service.delete_document(ctx, document_id, object_store=object_store)


async def move_document(
    document_id: uuid.UUID,
    req: DocumentMove,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> DocumentOut:
    return await documents_service.move_document(ctx, document_id, req.folder_id)


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
