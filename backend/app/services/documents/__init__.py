"""Documents/folders/tags use cases.

This package is a structural split of what used to be one flat ``service.py`` (258 lines
mixing folder CRUD, tag CRUD, and document upload/dedupe + the F20-F22 status-pipeline
transitions) — the refactor that introduced this split made ZERO logic changes. Each
sub-domain's methods live in their own module (``folders.py``/``tags.py``/``documents.py``)
as plain free functions taking ``ctx`` explicitly (the class held no instance state, so
this needed no self-like plumbing); ``DocumentsService`` here is pure delegation, so
``documents_service.create_folder(...)`` etc. resolve exactly as before. Same convention as
``app.services.ingestion`` — one composition pattern across the codebase, not two.
"""

from __future__ import annotations

import uuid

from app.middleware.context import TenantContext
from app.models.documents import DocumentOut, FolderCreate, FolderOut, TagCreate, TagOut
from app.services.documents import documents as _documents
from app.services.documents import folders as _folders
from app.services.documents import tags as _tags
from app.services.documents.exceptions import (
    DocumentNotFound as DocumentNotFound,
)
from app.services.documents.exceptions import (
    DocumentsError as DocumentsError,
)
from app.services.documents.exceptions import (
    FolderCycleError as FolderCycleError,
)
from app.services.documents.exceptions import (
    FolderNameConflict as FolderNameConflict,
)
from app.services.documents.exceptions import (
    FolderNotEmpty as FolderNotEmpty,
)
from app.services.documents.exceptions import (
    FolderNotFound as FolderNotFound,
)
from app.services.documents.exceptions import (
    TagNotFound as TagNotFound,
)
from app.services.documents.folders import DeleteMode
from app.services.storage import ObjectStore


class DocumentsService:
    async def create_folder(self, ctx: TenantContext, req: FolderCreate) -> FolderOut:
        return await _folders.create_folder(ctx, req)

    async def list_folders(self, ctx: TenantContext) -> list[FolderOut]:
        return await _folders.list_folders(ctx)

    async def get_folder(self, ctx: TenantContext, folder_id: uuid.UUID) -> FolderOut:
        return await _folders.get_folder(ctx, folder_id)

    async def set_restricted(
        self, ctx: TenantContext, folder_id: uuid.UUID, restricted: bool
    ) -> FolderOut:
        return await _folders.set_restricted(ctx, folder_id, restricted)

    async def delete_folder(
        self, ctx: TenantContext, folder_id: uuid.UUID, *, mode: DeleteMode = "block"
    ) -> None:
        return await _folders.delete_folder(ctx, folder_id, mode=mode)

    async def rename_folder(
        self, ctx: TenantContext, folder_id: uuid.UUID, new_name: str
    ) -> FolderOut:
        return await _folders.rename_folder(ctx, folder_id, new_name)

    async def move_folder(
        self, ctx: TenantContext, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None
    ) -> FolderOut:
        return await _folders.move_folder(ctx, folder_id, new_parent_id)

    async def create_tag(self, ctx: TenantContext, req: TagCreate) -> TagOut:
        return await _tags.create_tag(ctx, req)

    async def list_tags(self, ctx: TenantContext) -> list[TagOut]:
        return await _tags.list_tags(ctx)

    async def delete_tag(self, ctx: TenantContext, tag_id: uuid.UUID) -> None:
        return await _tags.delete_tag(ctx, tag_id)

    async def tag_document(
        self, ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> None:
        return await _tags.tag_document(ctx, document_id, tag_id)

    async def untag_document(
        self, ctx: TenantContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> None:
        return await _tags.untag_document(ctx, document_id, tag_id)

    async def list_documents(
        self,
        ctx: TenantContext,
        *,
        folder_id: uuid.UUID | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[DocumentOut]:
        return await _documents.list_documents(ctx, folder_id=folder_id, tag_id=tag_id)

    async def upload_document(
        self,
        ctx: TenantContext,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        folder_id: uuid.UUID | None,
        object_store: ObjectStore,
    ) -> tuple[DocumentOut, bool]:
        return await _documents.upload_document(
            ctx,
            filename=filename,
            content_type=content_type,
            data=data,
            folder_id=folder_id,
            object_store=object_store,
        )

    async def begin_parsing(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.begin_parsing(ctx, document_id)

    async def complete_parsing(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        language: str,
        page_count: int,
        artifact_key: str,
    ) -> DocumentOut:
        return await _documents.complete_parsing(
            ctx,
            document_id,
            language=language,
            page_count=page_count,
            artifact_key=artifact_key,
        )

    async def fail_stage(
        self, ctx: TenantContext, document_id: uuid.UUID, *, failed_stage: str, error_detail: str
    ) -> DocumentOut:
        return await _documents.fail_stage(
            ctx, document_id, failed_stage=failed_stage, error_detail=error_detail
        )

    async def begin_structuring(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.begin_structuring(ctx, document_id)

    async def complete_structuring(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.complete_structuring(ctx, document_id)

    async def begin_embedding(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.begin_embedding(ctx, document_id)

    async def complete_embedding(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.complete_embedding(ctx, document_id)

    async def get_document(self, ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
        return await _documents.get_document(ctx, document_id)

    async def list_by_ids(
        self, ctx: TenantContext, document_ids: list[uuid.UUID]
    ) -> list[DocumentOut]:
        return await _documents.list_by_ids(ctx, document_ids)

    async def get_parse_artifact_key(self, ctx: TenantContext, document_id: uuid.UUID) -> str:
        return await _documents.get_parse_artifact_key(ctx, document_id)

    async def delete_document(
        self, ctx: TenantContext, document_id: uuid.UUID, *, object_store: ObjectStore
    ) -> None:
        return await _documents.delete_document(ctx, document_id, object_store=object_store)


documents_service = DocumentsService()
