"""Chat HTTP routes. Thin (all logic in service) — also where the per-request
correlation_id is minted and bound into structlog's contextvars, so every log line
emitted anywhere during this request (service, retrieval, ingestion's search_chunks)
carries it automatically, and unbound again in `finally` so it never leaks into an
unrelated request sharing the same worker."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.controllers.deps import get_ctx
from app.platform.context import TenantContext
from app.platform.logging import get_logger
from app.platform.seams import LLM, Embedder, get_embedder, get_llm
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])
logger = get_logger(__name__)


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


@router.post("/stream")
async def stream_ask(
    req: ChatRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> StreamingResponse:
    """F4x SSE endpoint. Streams tokens as they arrive from the LLM, then sends a
    final ``done`` event with the persisted conversation/citations. Uses POST (not GET)
    so the query body is kept out of the URL; the frontend consumes via fetch+ReadableStream
    rather than ``EventSource`` (which only supports GET)."""
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    async def event_generator():
        try:
            async for event_dict in chat_service.stream_ask(
                ctx, req, embedder=embedder, llm=llm, correlation_id=correlation_id
            ):
                yield f"data: {json.dumps(event_dict)}\n\n"
        except Exception as exc:
            logger.error("chat.stream_failed", correlation_id=correlation_id, error=str(exc))
            yield 'data: {"type":"error","message":"Stream failed"}\n\n'
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
