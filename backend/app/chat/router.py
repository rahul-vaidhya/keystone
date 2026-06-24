"""Chat HTTP routes. Thin (all logic in service) — also where the per-request
correlation_id is minted and bound into structlog's contextvars, so every log line
emitted anywhere during this request (service, retrieval, ingestion's search_chunks)
carries it automatically, and unbound again in `finally` so it never leaks into an
unrelated request sharing the same worker."""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from app.chat.schemas import ChatRequest, ChatResponse
from app.chat.service import chat_service
from app.identity.deps import get_ctx
from app.platform.context import TenantContext
from app.platform.seams import LLM, Embedder, get_embedder, get_llm

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/ask", response_model=ChatResponse)
async def ask(
    req: ChatRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> ChatResponse:
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    try:
        return await chat_service.ask(
            ctx, req, embedder=embedder, llm=llm, correlation_id=correlation_id
        )
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")
