"""Ingestion HTTP handlers — manual trigger for pipeline stages. Thin (all logic in
service); production dispatch via arq tasks lands when a caller (e.g. enqueue-on-upload)
is built — not part of F20."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx, require_admin
from app.models.documents import DocumentOut
from app.models.ingestion import EnrichmentBackfillResult
from app.services.ingestion import ingestion_service
from app.services.seams import LLM, Embedder, Parser, get_embedder, get_llm, get_parser
from app.services.storage import ObjectStore, get_object_store  # noqa: F401


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
    llm: Annotated[LLM, Depends(get_llm)],
) -> DocumentOut:
    return await ingestion_service.run_structuring_stage(
        ctx, document_id, object_store=object_store, llm=llm
    )


async def embed_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> DocumentOut:
    return await ingestion_service.run_embedding_stage(ctx, document_id, embedder=embedder)


async def enrich_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    llm: Annotated[LLM, Depends(get_llm)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> DocumentOut:
    return await ingestion_service.run_enrichment_stage(
        ctx, document_id, llm=llm, embedder=embedder, object_store=object_store
    )


async def enrich_backfill(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    llm: Annotated[LLM, Depends(get_llm)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> EnrichmentBackfillResult:
    return await ingestion_service.run_enrichment_backfill(
        ctx, llm=llm, embedder=embedder, object_store=object_store
    )
