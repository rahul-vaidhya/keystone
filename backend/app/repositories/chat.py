"""Chat repositories — all SQL for conversations/messages lives here (codestandards
"Layering"). Insert-only: F41 never reads a conversation/message back (no history/list
endpoint exists yet — that's a future feature, not built speculatively here)."""

from __future__ import annotations

import uuid

from app.models.chat import Conversation, Message
from app.platform.repository import BaseRepository


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
