"""Retrieval HTTP handlers — manual search endpoint for testing before F40 chat exists.
Thin (all logic in service)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx
from app.models.retrieval import RetrievalSearchRequest, RetrievalSearchResponse
from app.services.retrieval import retrieval_service
from app.services.seams import Embedder, get_embedder


async def search(
    req: RetrievalSearchRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> RetrievalSearchResponse:
    return await retrieval_service.search(ctx, req, embedder=embedder)
