"""Ingestion HTTP handlers — manual trigger for pipeline stages. Thin (all logic in
service); production dispatch via arq tasks lands when a caller (e.g. enqueue-on-upload)
is built — not part of F20."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx
from app.models.documents import DocumentOut
from app.services.ingestion import ingestion_service
from app.services.seams import Embedder, Parser, get_embedder, get_parser
from app.services.storage import ObjectStore, get_object_store


async def parse_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    parser: Annotated[Parser, Depends(get_parser)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> DocumentOut:
    return await ingestion_service.run_parsing_stage(
        ctx, document_id, parser=parser, object_store=object_store
    )


async def structure_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> DocumentOut:
    return await ingestion_service.run_structuring_stage(
        ctx, document_id, object_store=object_store
    )


async def embed_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> DocumentOut:
    return await ingestion_service.run_embedding_stage(ctx, document_id, embedder=embedder)
