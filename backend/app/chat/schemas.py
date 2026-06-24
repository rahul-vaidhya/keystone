"""Chat (F40) Pydantic schemas. Stateless — no persistence yet (conversations/messages
land at F41/F42, per buildplan's F40 DoD which doesn't ask for storage)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.retrieval.schemas import ContextBlock


class ChatRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class ChatResponse(BaseModel):
    correlation_id: str
    notebook_id: uuid.UUID
    query: str
    answer: str
    citations: list[ContextBlock]
    model: str
