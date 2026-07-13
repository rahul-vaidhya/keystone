"""F40 grounded generation + F41 citations + F42 admin debug bundle: notebook-scoped
query -> F31 retrieval -> grounded prompt -> LLM seam -> answer -> citation resolution ->
persisted conversation+message+trace. Reaches retrieval ONLY through
``retrieval_service.search`` and chunk re-resolution ONLY through
``ingestion_service.get_chunks`` (module-boundary rule) — never reimplements
scoping/embedding/kNN search, never imports ingestion's models/repository.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator

from app.config import db as db_mod
from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.chat import (
    ChatRequest,
    ChatResponse,
    MessageTraceOut,
    ResolvedCitation,
)
from app.models.retrieval import ContextBlock, RetrievalSearchRequest
from app.services.chat.repository import ConversationRepository, MessageRepository, TraceRepository
from app.services.ingestion import ingestion_service
from app.services.retrieval import retrieval_service
from app.services.seams import LLM, Embedder, SeamTransientError
from app.services.seams import Message as SeamMessage

logger = get_logger(__name__)


# ---- exceptions ----
class GenerationFailed(RuntimeError):
    """The LLM seam call failed and retries were exhausted. Mapped to a 503 by
    `platform/http.py` — a clean, defined failure path (no uncaught exception), mirroring
    the ingestion stage failure-model discipline. Raised before any persistence happens
    (`ChatService.ask` calls the LLM before `_persist`), so there's no partial
    conversation/message row left behind on this failure path."""


class MessageTraceNotFound(RuntimeError):
    """F42: no trace exists for this message_id within the caller's org — either the
    message never belonged to this org, or it doesn't exist at all. Mapped to a 404."""


_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

_SYSTEM_PROMPT = (
    "You are a knowledge-base assistant. Answer ONLY using the numbered context blocks "
    "provided below the question. Cite the blocks you used by their number in square "
    "brackets, e.g. [1]. If the context does not contain enough information to answer the "
    "question, respond with exactly this sentence and nothing else: "
    '"I don\'t have that in the provided sources." Never use outside or prior knowledge.'
)


def build_messages(query: str, blocks: list[ContextBlock]) -> list[SeamMessage]:
    """Pure function — no DB, no seam. Formats the numbered context blocks (or none) into
    the prompt the grounding instruction above refers to."""
    if blocks:
        context_text = "\n\n".join(f"[{block.index}] {block.content}" for block in blocks)
    else:
        context_text = "(no context was retrieved for this notebook)"
    user_content = f"{context_text}\n\nQuestion: {query}"
    return [
        SeamMessage(role="system", content=_SYSTEM_PROMPT),
        SeamMessage(role="user", content=user_content),
    ]


def format_prompt_for_trace(messages: list[SeamMessage]) -> str:
    """Pure function — renders the exact message list sent to the LLM seam into the flat
    text the F42 debug bundle persists as ``final_prompt``."""
    return "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages)


async def generate_answer(messages: list[SeamMessage], *, llm: LLM) -> AsyncIterator[str]:
    """The streaming-ready core: F40 consumes this to completion; a future SSE router
    (F4x) consumes it incrementally instead, with no change to this function."""
    async for token in llm.stream(messages):
        yield token


async def call_llm_with_retry(messages: list[SeamMessage], *, llm: LLM, correlation_id: str) -> str:
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


def parse_citation_markers(answer: str) -> list[int]:
    """Pure function — extracts ``[n]`` markers from the model's answer, in order of
    first appearance, deduplicated. Malformed brackets (non-digit content, e.g. ``[abc]``)
    never match the digit-only regex, so they are simply absent from the result — not an
    error case to special-case."""
    seen: set[int] = set()
    markers: list[int] = []
    for match in _CITATION_MARKER_RE.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.add(n)
            markers.append(n)
    return markers


async def resolve_citations(
    ctx: TenantContext,
    *,
    answer: str,
    blocks: list[ContextBlock],
    correlation_id: str,
) -> list[ResolvedCitation]:
    """Maps the answer's ``[n]`` markers back to the ``ContextBlock``s actually sent in
    the prompt, then rebuilds each resolved citation from a FRESH
    ``ingestion_service.get_chunks`` read of the chunk row — the source-of-truth table —
    rather than trusting ``ContextBlock``'s self-reported fields. A marker that doesn't
    map to a block that was actually sent (out of range, or no blocks at all) is dropped
    silently from the result (logged, never raised, never fabricated) — same for a marker
    whose chunk no longer exists by the time of resolution."""
    markers = parse_citation_markers(answer)
    valid_markers = [m for m in markers if 1 <= m <= len(blocks)]
    invalid_markers = [m for m in markers if m not in valid_markers]

    block_by_marker = {m: blocks[m - 1] for m in valid_markers}
    chunk_ids = [block.chunk_id for block in block_by_marker.values()]
    records = await ingestion_service.get_chunks(ctx, chunk_ids)
    record_by_chunk_id = {record.chunk_id: record for record in records}

    resolved: list[ResolvedCitation] = []
    dropped_missing_chunk: list[int] = []
    for marker, block in block_by_marker.items():
        record = record_by_chunk_id.get(block.chunk_id)
        if record is None:
            dropped_missing_chunk.append(marker)
            continue
        resolved.append(
            ResolvedCitation(
                marker=marker,
                document_id=record.document_id,
                chunk_id=record.chunk_id,
                char_start=record.char_start,
                char_end=record.char_end,
                content=record.content,
            )
        )

    logger.info(
        "chat.citations_resolved",
        correlation_id=correlation_id,
        num_markers_parsed=len(markers),
        num_resolved=len(resolved),
        num_dropped_out_of_range=len(invalid_markers),
        num_dropped_missing_chunk=len(dropped_missing_chunk),
    )
    return resolved


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
        citations = await resolve_citations(
            ctx,
            answer=answer,
            blocks=retrieval_response.results,
            correlation_id=correlation_id,
        )

        conversation_id, message_id = await self._persist(
            ctx,
            req=req,
            answer=answer,
            citations=citations,
            hits=retrieval_response.results,
            final_prompt=format_prompt_for_trace(messages),
        )

        return ChatResponse(
            correlation_id=correlation_id,
            conversation_id=conversation_id,
            message_id=message_id,
            notebook_id=req.notebook_id,
            query=req.query,
            answer=answer,
            citations=citations,
            model=llm.model,
        )

    async def _persist(
        self,
        ctx: TenantContext,
        *,
        req: ChatRequest,
        answer: str,
        citations: list[ResolvedCitation],
        hits: list[ContextBlock],
        final_prompt: str,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Every ``/chat/ask`` call creates a FRESH conversation and its user/assistant
        message pair — no reuse across calls yet. Reuse only earns its place alongside
        multi-turn history-threading (a future feature); building append-to-conversation
        with no read side yet would be speculative storage (hard rule #8). Also writes the
        F42 debug bundle (``message_traces``) for the assistant message, in the same
        transaction — the trace dies with its message, never persisted separately."""
        async with db_mod.sessionmaker() as session, session.begin():
            conversation = await ConversationRepository(session, ctx).create(
                knowledge_base_id=req.notebook_id, user_id=ctx.user_id
            )
            await MessageRepository(session, ctx).create(
                conversation_id=conversation.id,
                role="user",
                content=req.query,
                citations=None,
            )
            assistant_message = await MessageRepository(session, ctx).create(
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
                citations=[c.model_dump(mode="json") for c in citations],
            )
            await TraceRepository(session, ctx).create(
                message_id=assistant_message.id,
                hits=[h.model_dump(mode="json") for h in hits],
                final_prompt=final_prompt,
                raw_output=answer,
            )
        return conversation.id, assistant_message.id

    async def stream_ask(
        self,
        ctx: TenantContext,
        req: ChatRequest,
        *,
        embedder: Embedder,
        llm: LLM,
        correlation_id: str,
    ) -> AsyncIterator[dict]:
        """SSE streaming variant of ``ask``: yields ``{"type":"token","content":"..."}``
        events as the LLM generates output, then a final ``{"type":"done",...}`` event
        carrying the persisted conversation/citations. No mid-stream retry — once tokens
        are flowing the client has partial output and a restart would confuse it."""
        retrieval_response = await retrieval_service.search(
            ctx,
            RetrievalSearchRequest(notebook_id=req.notebook_id, query=req.query, k=req.k),
            embedder=embedder,
        )
        logger.info(
            "chat.stream_context_assembled",
            correlation_id=correlation_id,
            notebook_id=str(req.notebook_id),
            num_blocks=len(retrieval_response.results),
        )
        messages = build_messages(req.query, retrieval_response.results)

        tokens: list[str] = []
        async for token in generate_answer(messages, llm=llm):
            tokens.append(token)
            yield {"type": "token", "content": token}

        answer = "".join(tokens).strip()
        citations = await resolve_citations(
            ctx,
            answer=answer,
            blocks=retrieval_response.results,
            correlation_id=correlation_id,
        )
        conversation_id, message_id = await self._persist(
            ctx,
            req=req,
            answer=answer,
            citations=citations,
            hits=retrieval_response.results,
            final_prompt=format_prompt_for_trace(messages),
        )
        yield {
            "type": "done",
            "correlation_id": correlation_id,
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "notebook_id": str(req.notebook_id),
            "query": req.query,
            "answer": answer,
            "citations": [c.model_dump(mode="json") for c in citations],
            "model": llm.model,
        }

    async def get_trace(self, ctx: TenantContext, message_id: uuid.UUID) -> MessageTraceOut:
        """F42: read-only, admin-gated at the controller (``require_admin``). Returns the
        trace verbatim from storage — never recomputed."""
        async with db_mod.sessionmaker() as session:
            trace = await TraceRepository(session, ctx).get_by_message_id(message_id)
        if trace is None:
            raise MessageTraceNotFound("Trace not found")
        return MessageTraceOut(
            id=trace.id,
            message_id=trace.message_id,
            hits=[ContextBlock.model_validate(h) for h in trace.hits],
            final_prompt=trace.final_prompt,
            raw_output=trace.raw_output,
            created_at=trace.created_at,
        )


chat_service = ChatService()
