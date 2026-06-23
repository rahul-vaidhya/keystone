"""Retrieval HTTP routes — manual search endpoint for testing before F40 chat exists.
Thin (all logic in service)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.identity.deps import get_ctx
from app.platform.context import TenantContext
from app.platform.seams import Embedder, get_embedder
from app.retrieval.schemas import RetrievalSearchRequest, RetrievalSearchResponse
from app.retrieval.service import retrieval_service

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/search", response_model=RetrievalSearchResponse)
async def search(
    req: RetrievalSearchRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> RetrievalSearchResponse:
    return await retrieval_service.search(ctx, req, embedder=embedder)
