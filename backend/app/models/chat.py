"""Chat domain models and Pydantic schemas.

ORM models: conversations + messages (architecture.md "chat"). F41 creates a fresh
``Conversation`` + its ``user``/``assistant`` ``Message`` pair on every ``/chat/ask``
call — no conversation reuse yet (see chat/service.py docstring on ``ChatService.ask`` for
why: reuse only earns its place alongside multi-turn history-threading, a future feature).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base
from app.models.retrieval import ContextBlock


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )  # [now] org_id on the child table too — no scope-via-parent (hard rule)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MessageTrace(Base):
    """F42 admin debug bundle — the persisted answer trace (hits/scores + final prompt +
    raw output), one row per message, dies with its message. Never recomputed; read-only,
    admin-gated at the route level."""

    __tablename__ = "message_traces"
    __table_args__ = (UniqueConstraint("message_id", name="uq_message_traces_message_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    hits: Mapped[list] = mapped_column(JSONB, nullable=False)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class ChatRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class ResolvedCitation(BaseModel):
    """One ``[n]`` marker from the model's answer, resolved to its source span — rebuilt
    from a fresh ``ingestion.service.get_chunks`` read of the chunk row (the
    source-of-truth table), not from the ``ContextBlock`` retrieval already had in hand.
    ``marker`` is the literal number the model cited (e.g. ``2`` for ``[2]``).
    ``page_start``/``page_end`` are the owning section's page range, shown alongside (not
    instead of) the char offsets so a non-technical reader has a recognizable reference —
    both are ``None`` when the source chunk has no section or the section has no page
    info, never fabricated."""

    marker: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    char_start: int
    char_end: int
    content: str
    page_start: int | None = None
    page_end: int | None = None


class ChatResponse(BaseModel):
    correlation_id: str
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    notebook_id: uuid.UUID
    query: str
    answer: str
    citations: list[ResolvedCitation]
    model: str


class MessageTraceOut(BaseModel):
    """F42 admin debug bundle response — the persisted trace for one message, verbatim
    (never recomputed)."""

    id: uuid.UUID
    message_id: uuid.UUID
    hits: list[ContextBlock]
    final_prompt: str
    raw_output: str
    created_at: datetime


class MessageOut(BaseModel):
    """One persisted message, returned by the notebook history endpoint (chat history
    hydration on navigation). ``citations`` round-trips straight from the JSONB column —
    it was written in exactly this ``ResolvedCitation`` shape by ``ChatService._persist``."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    citations: list[ResolvedCitation] | None
    created_at: datetime

    model_config = {"from_attributes": True}
