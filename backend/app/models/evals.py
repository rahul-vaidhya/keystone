"""Evals domain models and Pydantic schemas.

``GoldenQuestion`` is grown from a real graded ``/chat/ask`` answer via the admin Debug
panel's "Add to golden set" button — never hand-typed. ``reference_contexts`` snapshots
the retrieved chunk TEXT (not chunk ids), so a golden question stays gradable by the
opt-in ``pytest -m eval`` Ragas regression suite even after its source chunks are later
re-ingested or deleted. See migration ``0023`` for the full rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base


class GoldenQuestion(Base):
    __tablename__ = "golden_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ON DELETE SET NULL (not CASCADE): the golden question survives its origin
    # conversation being deleted — everything it needs to be graded is already
    # snapshotted onto this row (question/reference_answer/reference_contexts).
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False)
    # list[str] of snapshotted chunk texts (NOT chunk ids) — grading stays valid even if
    # the source chunks are later deleted/re-ingested.
    reference_contexts: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class GoldenQuestionCreate(BaseModel):
    """Everything but ``message_id`` is derived server-side from the message's trace
    (via ``chat_service.get_curation_snapshot``) — the caller only names which graded
    answer to curate."""

    message_id: uuid.UUID


class GoldenQuestionOut(BaseModel):
    id: uuid.UUID
    notebook_id: uuid.UUID
    source_message_id: uuid.UUID | None
    question: str
    reference_answer: str
    reference_contexts: list[str]
    status: str
    created_by: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
