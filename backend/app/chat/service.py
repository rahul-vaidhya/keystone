"""F40 grounded generation: notebook-scoped query -> F31 retrieval -> grounded prompt ->
LLM seam -> answer + carried citation data. Owns no table (stateless; see schemas.py).
Reaches retrieval ONLY through ``retrieval_service.search`` (module-boundary rule) — never
reimplements scoping/embedding/kNN search.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from app.chat.exceptions import GenerationFailed
from app.chat.schemas import ChatRequest, ChatResponse
from app.platform.config import settings
from app.platform.context import TenantContext
from app.platform.logging import get_logger
from app.platform.seams import LLM, Embedder, Message, SeamTransientError
from app.retrieval.schemas import ContextBlock, RetrievalSearchRequest
from app.retrieval.service import retrieval_service

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a knowledge-base assistant. Answer ONLY using the numbered context blocks "
    "provided below the question. Cite the blocks you used by their number in square "
    "brackets, e.g. [1]. If the context does not contain enough information to answer the "
    "question, respond with exactly this sentence and nothing else: "
    '"I don\'t have that in the provided sources." Never use outside or prior knowledge.'
)


def build_messages(query: str, blocks: list[ContextBlock]) -> list[Message]:
    """Pure function — no DB, no seam. Formats the numbered context blocks (or none) into
    the prompt the grounding instruction above refers to."""
    if blocks:
        context_text = "\n\n".join(f"[{block.index}] {block.content}" for block in blocks)
    else:
        context_text = "(no context was retrieved for this notebook)"
    user_content = f"{context_text}\n\nQuestion: {query}"
    return [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


async def generate_answer(messages: list[Message], *, llm: LLM) -> AsyncIterator[str]:
    """The streaming-ready core: F40 consumes this to completion; a future SSE router
    (F4x) consumes it incrementally instead, with no change to this function."""
    async for token in llm.stream(messages):
        yield token


async def call_llm_with_retry(messages: list[Message], *, llm: LLM, correlation_id: str) -> str:
    """Retries ONLY transient failures: our own timeout (`asyncio.timeout`) or
    `SeamTransientError` (raised by a real adapter for connection errors/5xx/429 — see
    `platform/seams/real_llm.py`). Anything else (a bug in prompt construction, an
    unexpected exception type) propagates immediately — it is not retried and does not
    become a buried 503."""
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            async with asyncio.timeout(settings.LLM_TIMEOUT_SECONDS):
                tokens = [token async for token in generate_answer(messages, llm=llm)]
            answer = "".join(tokens).strip()
        except (TimeoutError, SeamTransientError) as exc:
            will_retry = attempt <= settings.LLM_MAX_RETRIES
            logger.warning(
                "chat.llm_call_failed",
                correlation_id=correlation_id,
                attempt=attempt,
                error=str(exc),
                will_retry=will_retry,
            )
            if not will_retry:
                logger.error(
                    "chat.generation_failed", correlation_id=correlation_id, attempts=attempt
                )
                raise GenerationFailed("LLM seam unavailable after retries") from exc
            await asyncio.sleep(settings.LLM_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            continue

        latency_ms = (time.monotonic() - started) * 1000
        logger.info(
            "chat.llm_call_succeeded",
            correlation_id=correlation_id,
            model=llm.model,
            attempt=attempt,
            latency_ms=round(latency_ms, 1),
            answer_chars=len(answer),
        )
        return answer


class ChatService:
    async def ask(
        self,
        ctx: TenantContext,
        req: ChatRequest,
        *,
        embedder: Embedder,
        llm: LLM,
        correlation_id: str,
    ) -> ChatResponse:
        retrieval_response = await retrieval_service.search(
            ctx,
            RetrievalSearchRequest(notebook_id=req.notebook_id, query=req.query, k=req.k),
            embedder=embedder,
        )
        logger.info(
            "chat.context_assembled",
            correlation_id=correlation_id,
            notebook_id=str(req.notebook_id),
            num_blocks=len(retrieval_response.results),
            chunk_ids=[str(block.chunk_id) for block in retrieval_response.results],
            distances=[block.distance for block in retrieval_response.results],
        )

        messages = build_messages(req.query, retrieval_response.results)
        answer = await call_llm_with_retry(messages, llm=llm, correlation_id=correlation_id)

        return ChatResponse(
            correlation_id=correlation_id,
            notebook_id=req.notebook_id,
            query=req.query,
            answer=answer,
            citations=retrieval_response.results,
            model=llm.model,
        )


chat_service = ChatService()
