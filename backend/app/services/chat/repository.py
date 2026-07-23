"""Chat repositories — all SQL for conversations/messages/message_traces lives here
(codestandards "Layering"). Every ``/chat/ask`` or ``/chat/stream`` call writes all three
tables in one transaction (``service.py``'s ``_persist``): a conversation, its user and
assistant messages, and the assistant message's F42 debug-bundle trace.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.chat import Conversation, Message, MessageTrace
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
    ) -> Message:
        message = Message(
            org_id=self._ctx.org_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations,
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
            .order_by(Message.created_at.asc())
        )
        return list(await self._db.scalars(stmt))


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
