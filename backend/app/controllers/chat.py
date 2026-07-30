"""Chat HTTP handlers. Thin (all logic in service) — also where the per-request
correlation_id is minted and bound into structlog's contextvars, so every log line
emitted anywhere during this request (service, retrieval, ingestion's search_chunks)
carries it automatically, and unbound again in `finally` so it never leaks into an
unrelated request sharing the same worker."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

import structlog
from fastapi import Depends
from fastapi.responses import StreamingResponse

from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.middleware.deps import get_ctx, require_admin
from app.models.chat import (
    ChatRequest,
    ChatResponse,
    FeedbackCreate,
    FeedbackOut,
    MessageOut,
    MessageTraceOut,
)
from app.services.chat import chat_service
from app.services.seams import LLM, Embedder, Reranker, get_embedder, get_llm, get_reranker

logger = get_logger(__name__)


async def ask(
    req: ChatRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    reranker: Annotated[Reranker, Depends(get_reranker)],
) -> ChatResponse:
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    try:
        return await chat_service.ask(
            ctx, req, embedder=embedder, llm=llm, reranker=reranker, correlation_id=correlation_id
        )
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")


async def stream_ask(
    req: ChatRequest,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    reranker: Annotated[Reranker, Depends(get_reranker)],
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
                ctx,
                req,
                embedder=embedder,
                llm=llm,
                reranker=reranker,
                correlation_id=correlation_id,
            ):
                yield f"data: {json.dumps(event_dict)}\n\n"
        except Exception as exc:
            logger.error(
                "chat.stream_failed",
                correlation_id=correlation_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
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


async def get_trace(
    message_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> MessageTraceOut:
    """F42 admin debug bundle. Owner/admin only (``require_admin``)."""
    return await chat_service.get_trace(ctx, message_id)


async def list_messages(
    notebook_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> list[MessageOut]:
    """Chat history hydration for a notebook — every member can see it, same access
    level as asking a question in the notebook."""
    return await chat_service.list_messages(ctx, notebook_id)


async def submit_feedback(
    message_id: uuid.UUID,
    req: FeedbackCreate,
    ctx: Annotated[TenantContext, Depends(get_ctx)],
) -> FeedbackOut:
    """Rate an assistant message (thumbs up/down). Any authenticated org member may
    call this — the real gate is inside the service (the same notebook-visibility
    check ``list_messages`` performs, via ``knowledge_service.get_notebook``)."""
    return await chat_service.submit_feedback(ctx, message_id, req)
