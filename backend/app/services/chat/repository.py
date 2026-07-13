"""Chat repositories — all SQL for conversations/messages/message_traces lives here
(codestandards "Layering"). Every ``/chat/ask`` or ``/chat/stream`` call writes all three
tables in one transaction (``service.py``'s ``_persist``): a conversation, its user and
assistant messages, and the assistant message's F42 debug-bundle trace.
"""

from __future__ import annotations

import uuid

from app.models.chat import Conversation, Message, MessageTrace
from app.services.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def create(
        self, *, knowledge_base_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> Conversation:
        conversation = Conversation(
            org_id=self._ctx.org_id, knowledge_base_id=knowledge_base_id, user_id=user_id
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
