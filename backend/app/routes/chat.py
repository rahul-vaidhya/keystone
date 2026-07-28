"""Chat routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.chat import ChatResponse, FeedbackOut, MessageOut, MessageTraceOut

router = APIRouter(prefix="/chat", tags=["chat"])

router.post("/ask", response_model=ChatResponse)(controllers.chat.ask)
router.post("/stream")(controllers.chat.stream_ask)
router.get("/messages/{message_id}/trace", response_model=MessageTraceOut)(
    controllers.chat.get_trace
)
router.get("/notebooks/{notebook_id}/messages", response_model=list[MessageOut])(
    controllers.chat.list_messages
)
router.post("/messages/{message_id}/feedback", response_model=FeedbackOut)(
    controllers.chat.submit_feedback
)
