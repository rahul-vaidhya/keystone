"""Chat routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.chat import ChatResponse, MessageTraceOut

router = APIRouter(prefix="/chat", tags=["chat"])

router.post("/ask", response_model=ChatResponse)(controllers.chat.ask)
router.post("/stream")(controllers.chat.stream_ask)
router.get("/messages/{message_id}/trace", response_model=MessageTraceOut)(
    controllers.chat.get_trace
)
