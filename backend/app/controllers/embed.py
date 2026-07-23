"""Embed widget HTTP handlers — thin; all logic in service.

Admin endpoints require auth + ``require_admin`` (mirrors the access-roles/chat-trace
precedent). Public endpoints take NO auth dependency at all (the signup/login/
accept-invite precedent) — anonymous visitors on a customer's website never have a
Veratas account.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.responses import StreamingResponse

from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.middleware.deps import require_admin
from app.models.embed import (
    EmbedChatRequest,
    EmbedConfigOut,
    WidgetCreateRequest,
    WidgetOut,
    WidgetUpdateRequest,
)
from app.services.embed import embed_service
from app.services.seams import LLM, Embedder, get_embedder, get_llm
from app.utils.rate_limit import RateLimiter, get_rate_limiter

logger = get_logger(__name__)


async def create_widget(
    req: WidgetCreateRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> WidgetOut:
    return await embed_service.create_widget(ctx, req)


async def list_widgets(
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> list[WidgetOut]:
    return await embed_service.list_widgets(ctx)


async def update_widget(
    widget_id: uuid.UUID,
    req: WidgetUpdateRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> WidgetOut:
    return await embed_service.update_widget(ctx, widget_id, req)


async def delete_widget(
    widget_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> None:
    await embed_service.delete_widget(ctx, widget_id)


async def get_public_config(org_id: uuid.UUID, public_id: str) -> EmbedConfigOut:
    """No auth dependency — public endpoint, same shape as signup/login/accept-invite."""
    return await embed_service.get_public_config(org_id, public_id)


async def stream_public_chat(
    org_id: uuid.UUID,
    public_id: str,
    req: EmbedChatRequest,
    request: Request,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    rate_limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> StreamingResponse:
    """No auth dependency — public endpoint. Mirrors ``controllers.chat.stream_ask``'s
    SSE shape exactly. ``embed_service.public_chat_stream`` is AWAITED here (it is a
    plain coroutine, not an async generator) so its eager widget/origin/rate-limit
    validation runs and can raise BEFORE this function ever constructs the
    ``StreamingResponse`` — a 404/403/429 from that call becomes a real HTTP status via
    the registered exception handlers, never a mid-stream SSE error event."""
    client_ip = request.client.host if request.client else "unknown"
    correlation_id = str(uuid.uuid4())

    event_stream = await embed_service.public_chat_stream(
        org_id,
        public_id,
        req,
        embedder=embedder,
        llm=llm,
        rate_limiter=rate_limiter,
        client_ip=client_ip,
    )

    async def event_generator():
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            async for event_dict in event_stream:
                yield f"data: {json.dumps(event_dict)}\n\n"
        except Exception as exc:
            logger.error("embed.stream_failed", correlation_id=correlation_id, error=str(exc))
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
