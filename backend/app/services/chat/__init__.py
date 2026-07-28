"""Chat use cases + repositories (F40 grounded generation, F41 citations, F42 admin
debug bundle).

This package is a structural split of what used to be one flat ``chat.py`` (435 lines
mixing the ``Conversation``/``Message``/``MessageTrace`` repository classes with the
generation pipeline and exceptions) — the refactor that introduced this split made ZERO
logic changes. SQL lives in ``repository.py``; business logic (prompt building, LLM
retry, citation resolution, ``ChatService``) lives in ``service.py``. Same convention as
``app.services.ingestion``/``app.services.documents``.
"""

from __future__ import annotations

from app.services.chat.service import CannotCurateUserMessage as CannotCurateUserMessage
from app.services.chat.service import FeedbackOnUserMessage as FeedbackOnUserMessage
from app.services.chat.service import GenerationFailed as GenerationFailed
from app.services.chat.service import MessageNotFound as MessageNotFound
from app.services.chat.service import MessageTraceNotFound as MessageTraceNotFound
from app.services.chat.service import build_messages as build_messages
from app.services.chat.service import chat_service as chat_service
