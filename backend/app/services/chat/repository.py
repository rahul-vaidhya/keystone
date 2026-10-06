"""Chat repositories — all SQL for conversations/messages/message_traces lives here
(codestandards "Layering"). Every ``/chat/ask`` or ``/chat/stream`` call writes all three
tables in one transaction (``service.py``'s ``_persist``): a conversation, its user and
assistant messages, and the assistant message's F42 debug-bundle trace.
"""

from __future__ import annotations

import uuid

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.chat import Conversation, Message, MessageFeedback, MessageTrace
from app.services.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def create(
        self,
        *,
        knowledge_base_id: uuid.UUID,
        user_id: uuid.UUID | None,
        widget_id: uuid.UUID | None = None,
    ) -> Conversation:
        conversation = Conversation(
            org_id=self._ctx.org_id,
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            widget_id=widget_id,
        )
        self._db.add(conversation)
        await self._db.flush()
        return conversation


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        citations: list[dict] | None,
        claim_checks: list[dict] | None = None,
    ) -> Message:
        message = Message(
            org_id=self._ctx.org_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations,
            claim_checks=claim_checks,
        )
        self._db.add(message)
        await self._db.flush()
        return message

    async def list_for_notebook(self, knowledge_base_id: uuid.UUID) -> list[Message]:
        """History hydration for a notebook: every message from every (fresh,
        non-reused — see ``ChatService.ask``'s docstring) conversation that belongs to
        this notebook, oldest first. Joins to ``Conversation`` to filter by
        ``knowledge_base_id`` but scopes BOTH tables by ``org_id`` independently — the
        join alone is never trusted as the isolation boundary (hard rule: every query
        scoped by org_id)."""
        stmt = (
            self._scoped(select(Message))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.knowledge_base_id == knowledge_base_id,
                Conversation.org_id == self._ctx.org_id,
            )
            # A user/assistant pair is written in one transaction, so both rows share
            # the same ``created_at`` (transaction timestamp). Without tiebreakers the
            # pair's order is nondeterministic — keep each conversation's messages
            # together, user question before assistant answer, then id for stability.
            .order_by(
                Message.created_at.asc(),
                Message.conversation_id.asc(),
                case((Message.role == "user", 0), else_=1).asc(),
                Message.id.asc(),
            )
        )
        return list(await self._db.scalars(stmt))

    async def get_user_question_in_conversation(self, conversation_id: uuid.UUID) -> Message | None:
        """The paired user question for an assistant answer looked up via
        ``get_with_notebook_id`` — every ``/chat/ask``/``/chat/stream`` call creates a
        FRESH conversation with exactly one user message + one assistant message (see
        ``ChatService.ask``'s docstring: no conversation reuse), so this is simply "the
        other message in the same conversation." Org-scoped independently, same
        precedent as every other repository method here. ``None`` only if the
        conversation somehow has no user message (shouldn't happen given the invariant
        above, but never assumed)."""
        stmt = (
            self._scoped()
            .where(Message.conversation_id == conversation_id, Message.role == "user")
            .order_by(Message.created_at.asc())
            .limit(1)
        )
        return await self._db.scalar(stmt)

    async def get_with_notebook_id(self, message_id: uuid.UUID) -> tuple[Message, uuid.UUID] | None:
        """Joins to ``Conversation`` to also return its ``knowledge_base_id`` (the
        notebook the message's conversation belongs to) — mirrors ``list_for_notebook``'s
        join shape. Both ``Message`` and ``Conversation`` are scoped by ``org_id``
        independently; the join alone is never trusted as the isolation boundary.
        Returns ``None`` if the message doesn't exist in this org (RLS + the org_id
        filter handle "wrong org" naturally, same as any other not-found case)."""
        stmt = (
            select(Message, Conversation.knowledge_base_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == message_id,
                Message.org_id == self._ctx.org_id,
                Conversation.org_id == self._ctx.org_id,
            )
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            return None
        message, notebook_id = row
        return message, notebook_id


class TraceRepository(BaseRepository[MessageTrace]):
    model = MessageTrace

    async def create(
        self,
        *,
        message_id: uuid.UUID,
        hits: list[dict],
        final_prompt: str,
        raw_output: str,
    ) -> MessageTrace:
        trace = MessageTrace(
            org_id=self._ctx.org_id,
            message_id=message_id,
            hits=hits,
            final_prompt=final_prompt,
            raw_output=raw_output,
        )
        self._db.add(trace)
        await self._db.flush()
        return trace

    async def get_by_message_id(self, message_id: uuid.UUID) -> MessageTrace | None:
        result = await self._db.scalars(self._scoped().where(MessageTrace.message_id == message_id))
        return result.first()


class FeedbackRepository(BaseRepository[MessageFeedback]):
    model = MessageFeedback

    async def upsert(
        self,
        *,
        message_id: uuid.UUID,
        user_id: uuid.UUID,
        rating: str,
        reason_tags: list[str] | None,
        comment: str | None,
        corrected_answer: str | None,
    ) -> MessageFeedback:
        """Upsert on ``unique(message_id, user_id)`` — ONE current rating per user per
        message, not an audit log of every click. A user re-rating the same message
        updates the existing row's rating/reason_tags/comment/corrected_answer in place."""
        stmt = (
            pg_insert(MessageFeedback)
            .values(
                org_id=self._ctx.org_id,
                message_id=message_id,
                user_id=user_id,
                rating=rating,
                reason_tags=reason_tags or [],
                comment=comment,
                corrected_answer=corrected_answer,
            )
            .on_conflict_do_update(
                index_elements=["message_id", "user_id"],
                set_={
                    "rating": rating,
                    "reason_tags": reason_tags or [],
                    "comment": comment,
                    "corrected_answer": corrected_answer,
                },
            )
            .returning(MessageFeedback)
        )
        result = await self._db.execute(stmt)
        await self._db.flush()
        return result.scalar_one()

    async def get_for_messages(
        self, message_ids: list[uuid.UUID], user_id: uuid.UUID
    ) -> dict[uuid.UUID, str]:
        """``message_id -> rating``, scoped to ONE user (never another user's rating on
        the same message) — the history-hydration join source for
        ``MessageOut.my_feedback``."""
        if not message_ids:
            return {}
        stmt = self._scoped().where(
            MessageFeedback.message_id.in_(message_ids),
            MessageFeedback.user_id == user_id,
        )
        rows = await self._db.scalars(stmt)
        return {row.message_id: row.rating for row in rows}
