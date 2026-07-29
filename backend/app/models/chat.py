"""Chat domain models and Pydantic schemas.

ORM models: conversations + messages (architecture.md "chat"). F41 creates a fresh
``Conversation`` + its ``user``/``assistant`` ``Message`` pair on every ``/chat/ask``
call — no conversation reuse yet (see chat/service.py docstring on ``ChatService.ask`` for
why: reuse only earns its place alongside multi-turn history-threading, a future feature).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base
from app.models.retrieval import ContextBlock, SynthesisBlock


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
    # Nullable FK to widgets.id (migration 0019) — set only for conversations that
    # originated from an anonymous embed-widget visitor (never authenticated chat).
    # ON DELETE SET NULL: deleting a widget must never delete its conversation history.
    widget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widgets.id", ondelete="SET NULL"), nullable=True
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


class MessageFeedback(Base):
    """Per-user rating on an assistant message (thumbs up/down) — one row per
    ``(message_id, user_id)``, the upsert target: a user re-rating the same message
    updates their existing row rather than inserting a second one (not an audit log of
    every click). Dies with its message (same precedent as ``MessageTrace``).
    ``reason_tags``/``comment``/``corrected_answer`` exist schema-ready for a FUTURE
    admin labeling UI — only ``rating`` is populated by the wired-up thumbs buttons
    today."""

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
    )

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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[str] = mapped_column(Text, nullable=False)  # 'up' | 'down'
    reason_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class ChatRequest(BaseModel):
    notebook_id: uuid.UUID
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class ResolvedCitation(BaseModel):
    """One ``[n]`` marker from the model's answer, resolved to its source span. Two
    citation paths share this ONE shape (additive — the P1 broad-query router, see
    ``citation_type`` below, never a new parallel type):

    * ``citation_type="chunk"`` (default, the original F41 shape, byte-identical):
      rebuilt from a fresh ``ingestion.service.get_chunks`` read of the chunk row (the
      source-of-truth table), not from the ``ContextBlock`` retrieval already had in
      hand. ``chunk_id``/``char_start``/``char_end`` are ALWAYS populated on this path —
      they are only structurally nullable (typed ``| None``) to let the section path
      below omit them, never actually ``None`` here.
    * ``citation_type="section"`` (P1 broad-query map-reduce, ``app.services.chat.
      broad_query``): the answer was synthesized from section summaries, not individual
      chunks, so there is no single char span to cite — ``chunk_id``/``char_start``/
      ``char_end`` are ``None`` and ``section_id``/``heading`` are populated instead.

    ``marker`` is the literal number the model cited (e.g. ``2`` for ``[2]``).
    ``page_start``/``page_end`` are the owning section's page range on the chunk path,
    shown alongside (not instead of) the char offsets so a non-technical reader has a
    recognizable reference — both are ``None`` when the source chunk has no section or
    the section has no page info, never fabricated; always ``None`` on the section
    path (a synthesized section-level answer has no single page to point at)."""

    marker: int
    document_id: uuid.UUID
    # Nullable only to allow the section path to omit them (see class docstring) — the
    # unchanged chunk path always populates all three with a real value.
    chunk_id: uuid.UUID | None = None
    char_start: int | None = None
    char_end: int | None = None
    content: str
    page_start: int | None = None
    page_end: int | None = None
    # Additive (P1): "chunk" (default, preserves every existing citation byte-identical)
    # or "section" (P1 broad-query map-reduce).
    citation_type: Literal["chunk", "section"] = "chunk"
    # Populated ONLY on the section path (P1 broad-query map-reduce) — always None on
    # the unchanged chunk path.
    section_id: uuid.UUID | None = None
    heading: str | None = None


class ChatResponse(BaseModel):
    correlation_id: str
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    notebook_id: uuid.UUID
    query: str
    answer: str
    citations: list[ResolvedCitation]
    model: str
    # Reranker-score confidence gate: True when the top retrieved block's rerank_score
    # fell below settings.RERANK_MIN_SCORE and the LLM call was skipped entirely in favor
    # of a fixed "weak evidence" answer. Always False on the normal (LLM-answered) path,
    # including every response while RERANKER_ENABLED=False (rerank_score is always None
    # then, so the gate structurally cannot fire).
    weak_evidence: bool = False


class MessageTraceOut(BaseModel):
    """F42 admin debug bundle response — the persisted trace for one message, verbatim
    (never recomputed). ``hits`` is additive (P1): a ``ContextBlock`` list for the
    unchanged chunk/flat/hybrid/rerank path (every existing trace, byte-identical), or a
    ``SynthesisBlock`` list for the P1 broad-query map-reduce path — never a mix within
    one message, since one message is answered by exactly one strategy. Pydantic
    disambiguates the two on load (``ChatService.get_trace``) since their required
    fields don't overlap (``chunk_id``/``char_start``/``char_end`` vs.
    ``section_ids``/``headings``/``document_ids``)."""

    id: uuid.UUID
    message_id: uuid.UUID
    hits: list[ContextBlock] | list[SynthesisBlock]
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
    # The CALLING user's own prior rating on this message — never anyone else's, never
    # an aggregate. None when this user hasn't rated it (or the message is a 'user' one,
    # which can never be rated). Populated by ChatService.list_messages from a
    # FeedbackRepository.get_for_messages read scoped to ctx.user_id.
    my_feedback: Literal["up", "down"] | None = None

    model_config = {"from_attributes": True}


class FeedbackCreate(BaseModel):
    """Only ``rating`` is populated by the wired-up thumbs buttons this round —
    ``reason_tags``/``comment``/``corrected_answer`` exist schema-ready for a FUTURE
    admin labeling UI, unpopulated by any UI today."""

    rating: Literal["up", "down"]
    reason_tags: list[str] | None = None
    comment: str | None = None
    corrected_answer: str | None = None


class FeedbackOut(BaseModel):
    """The persisted feedback row, echoed back verbatim after an upsert."""

    id: uuid.UUID
    message_id: uuid.UUID
    user_id: uuid.UUID
    rating: Literal["up", "down"]
    reason_tags: list[str]
    comment: str | None
    corrected_answer: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CurationSnapshot(BaseModel):
    """The ONLY shape ``app.services.evals`` reads chat data through — returned by
    ``ChatService.get_curation_snapshot`` (module-boundary rule: evals never imports
    chat's repository classes or ORM models directly). A point-in-time snapshot of one
    graded ``/chat/ask`` answer, structured for ``evals.service`` to persist as a
    ``GoldenQuestion`` row: ``question`` is the paired user message in the same
    (never-reused — see ``ChatService.ask``'s docstring) conversation,
    ``reference_answer`` is the assistant message's content, and
    ``reference_contexts`` is the list of retrieved chunk TEXT from the message's
    persisted trace (``hit["content"]`` for each hit) — not chunk ids, so the golden
    question stays gradable even after its source chunks are re-ingested or deleted."""

    notebook_id: uuid.UUID
    question: str
    reference_answer: str
    reference_contexts: list[str]

    model_config = {"from_attributes": True}
