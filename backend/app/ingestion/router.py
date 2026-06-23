"""Ingestion HTTP routes — manual trigger for pipeline stages. Thin (all logic in
service); production dispatch via arq tasks lands when a caller (e.g. enqueue-on-upload)
is built — not part of F20."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.documents.schemas import DocumentOut
from app.identity.deps import get_ctx
from app.ingestion.service import ingestion_service
from app.platform.context import TenantContext
from app.platform.seams import Parser, get_parser
from app.platform.storage import ObjectStore, get_object_store

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/documents/{document_id}/parse", response_model=DocumentOut)
async def parse_document(
    document_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    parser: Annotated[Parser, Depends(get_parser)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> DocumentOut:
    return await ingestion_service.run_parsing_stage(
        ctx, document_id, parser=parser, object_store=object_store
    )
