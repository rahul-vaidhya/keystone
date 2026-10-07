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

from pydantic import TypeAdapter

from app.config import db as db_mod
from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.chat import (
    ChatRequest,
    ChatResponse,
    ClaimCheck,
    CurationSnapshot,
    FeedbackCreate,
    FeedbackOut,
    MessageOut,
    MessageTraceOut,
    ResolvedCitation,
)
from app.models.retrieval import ContextBlock, RetrievalSearchRequest, SynthesisBlock
from app.services.chat.broad_query import try_broad_query
from app.services.chat.citation_check import check_claims
from app.services.chat.repository import (
    ConversationRepository,
    FeedbackRepository,
    MessageRepository,
    TraceRepository,
)
from app.services.ingestion import ingestion_service
from app.services.knowledge import knowledge_service
from app.services.retrieval import retrieval_service
from app.services.seams import LLM, Embedder, Reranker, SeamTransientError
from app.services.seams import Message as SeamMessage

logger = get_logger(__name__)

_HIT_ADAPTER: TypeAdapter[list[ContextBlock] | list[SynthesisBlock]] = TypeAdapter(
    list[ContextBlock] | list[SynthesisBlock]
)


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


class MessageNotFound(RuntimeError):
    """No message exists with this message_id within the caller's org — either it
    never belonged to this org or it doesn't exist at all (same "not found means
    either" shape as ``MessageTraceNotFound``). Mapped to a 404."""


class FeedbackOnUserMessage(RuntimeError):
    """Raised when feedback targets a ``role='user'`` message — only assistant answers
    can be rated; a user can't thumbs-up/down their own question. Mapped to a 400."""


class CannotCurateUserMessage(RuntimeError):
    """Raised when a golden-question curation request (``get_curation_snapshot``)
    targets a ``role='user'`` message. Deliberately NOT reusing ``FeedbackOnUserMessage``
    even though both map to the same 400 status and both fire on the same role check:
    the two are semantically distinct failures on distinct endpoints — "you can't
    rate your own question" (feedback) versus "a question has no answer/trace of its
    own to snapshot as a golden question" (curation) — and collapsing them would make
    a future change to one message/endpoint silently affect the other. Mapped to a
    400."""


_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

_WEAK_EVIDENCE_MESSAGE = "The available sources don't contain a strong match for this question."

_SYSTEM_PROMPT = (
    "You are a knowledge-base assistant. Answer ONLY using the numbered context blocks "
    "provided below the question. Cite the blocks you used by their number in square "
    "brackets, e.g. [1]. If some blocks are relevant to the question, answer from what they "
    "say, even if they only cover part of it. Only if no block is relevant to the question, "
    "respond with exactly this sentence and nothing else: "
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


def _weak_evidence_gate_fires(blocks: list[ContextBlock]) -> bool:
    """The reranker-score confidence gate (locked design — see ``RERANK_MIN_SCORE``'s
    docstring in ``config/settings.py``): fires only when the TOP block carries a real
    ``rerank_score`` below the threshold. Empty ``blocks`` (zero retrieval hits) never
    fires this gate — that's the existing "no context -> LLM's own refusal" path,
    deliberately left untouched. ``rerank_score`` is ``None`` on every block whenever
    ``RERANKER_ENABLED=False``, so this structurally cannot fire without the reranker
    feature also being on — no separate enable flag needed."""
    return (
        bool(blocks)
        and blocks[0].rerank_score is not None
        and blocks[0].rerank_score < settings.RERANK_MIN_SCORE
    )


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
                page_start=record.page_start,
                page_end=record.page_end,
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
        reranker: Reranker,
        correlation_id: str,
    ) -> ChatResponse:
        # P1 broad-query router (memory.md "P1 roadmap"): when the flag is off (the
        # default), this block is skipped entirely — zero extra DB/LLM calls, the code
        # below is byte-identical to before this feature existed.
        if settings.BROAD_QUERY_ENABLED:
            scope = await retrieval_service.resolve_notebook_scope(ctx, req.notebook_id)
            broad = await try_broad_query(ctx, scope, req.query, llm=llm)
            if broad is not None:
                logger.info(
                    "chat.broad_query_used",
                    correlation_id=correlation_id,
                    notebook_id=str(req.notebook_id),
                    num_blocks=len(broad.hits),
                )
                conversation_id, message_id = await self._persist(
                    ctx,
                    req=req,
                    answer=broad.answer,
                    citations=broad.citations,
                    hits=broad.hits,
                    final_prompt=broad.final_prompt,
                )
                return ChatResponse(
                    correlation_id=correlation_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    notebook_id=req.notebook_id,
                    query=req.query,
                    answer=broad.answer,
                    citations=broad.citations,
                    model=llm.model,
                    weak_evidence=False,
                )

        retrieval_response = await retrieval_service.search(
            ctx,
            RetrievalSearchRequest(notebook_id=req.notebook_id, query=req.query, k=req.k),
            embedder=embedder,
            reranker=reranker,
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

        if _weak_evidence_gate_fires(retrieval_response.results):
            logger.info(
                "chat.weak_evidence_gate_fired",
                correlation_id=correlation_id,
                notebook_id=str(req.notebook_id),
                top_rerank_score=retrieval_response.results[0].rerank_score,
                threshold=settings.RERANK_MIN_SCORE,
            )
            answer = _WEAK_EVIDENCE_MESSAGE
            citations: list[ResolvedCitation] = []
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
                weak_evidence=True,
            )

        answer = await call_llm_with_retry(messages, llm=llm, correlation_id=correlation_id)
        citations = await resolve_citations(
            ctx,
            answer=answer,
            blocks=retrieval_response.results,
            correlation_id=correlation_id,
        )
        claim_checks = await self._maybe_check_claims(
            ctx,
            req=req,
            answer=answer,
            blocks=retrieval_response.results,
            embedder=embedder,
            correlation_id=correlation_id,
        )

        conversation_id, message_id = await self._persist(
            ctx,
            req=req,
            answer=answer,
            citations=citations,
            hits=retrieval_response.results,
            final_prompt=format_prompt_for_trace(messages),
            claim_checks=claim_checks,
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
            weak_evidence=False,
            claim_checks=claim_checks,
        )

    async def _maybe_check_claims(
        self,
        ctx: TenantContext,
        *,
        req: ChatRequest,
        answer: str,
        blocks: list[ContextBlock],
        embedder: Embedder,
        correlation_id: str,
    ) -> list[ClaimCheck] | None:
        """Per-sentence citation check (``citation_check``) -- ``None`` (and zero extra
        work) when ``CITATION_CHECK_ENABLED`` is off, so the default path is unchanged.
        Only runs on the normal chunk-cited LLM path (never the weak-evidence gate or the
        broad-query section path). Never raises: a sparse-index or embedder failure only
        nulls the respective score."""
        if not settings.CITATION_CHECK_ENABLED:
            return None
        index = None
        try:
            document_ids = await retrieval_service.resolve_notebook_scope(ctx, req.notebook_id)
            if document_ids:
                index = (await retrieval_service.get_sparse_index(ctx, document_ids)).index
                # idf = log(N/df) is 0 for every term when N < 2, so tf-idf cosine is
                # meaningless on a 1-chunk corpus -- report lexical as unavailable.
                if index.n_docs < 2:
                    index = None
        except Exception as exc:  # noqa: BLE001 -- a check must never fail the chat turn
            logger.warning(
                "chat.citation_check_index_failed",
                correlation_id=correlation_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        try:
            return await check_claims(
                answer, blocks, index=index, embedder=embedder, correlation_id=correlation_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "chat.citation_check_failed",
                correlation_id=correlation_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def _persist(
        self,
        ctx: TenantContext,
        *,
        req: ChatRequest,
        answer: str,
        citations: list[ResolvedCitation],
        hits: list[ContextBlock] | list[SynthesisBlock],
        final_prompt: str,
        widget_id: uuid.UUID | None = None,
        claim_checks: list[ClaimCheck] | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Every ``/chat/ask`` call creates a FRESH conversation and its user/assistant
        message pair — no reuse across calls yet. Reuse only earns its place alongside
        multi-turn history-threading (a future feature); building append-to-conversation
        with no read side yet would be speculative storage (hard rule #8). Also writes the
        F42 debug bundle (``message_traces``) for the assistant message, in the same
        transaction — the trace dies with its message, never persisted separately.
        ``widget_id`` (default ``None``) marks a conversation as widget-originated — only
        ever passed by ``stream_ask`` when called from the embed widget's public
        endpoint; the authenticated ``ask``/``stream_ask`` paths never pass it, so this
        stays ``None`` and the existing chat behavior is byte-identical.

        ``hits`` is additive (P1 broad-query router): ``list[ContextBlock]`` on the
        unchanged flat/hierarchical/hybrid/rerank path (every call site before this
        feature existed, byte-identical), or ``list[SynthesisBlock]`` on the P1
        broad-query map-reduce path — either way this method just ``model_dump``s each
        hit generically, exactly as it always has, so this widened type hint is the only
        change needed here (no new parameter, no behavior change on the existing path)."""
        async with db_mod.tenant_session(ctx.org_id) as session:
            conversation = await ConversationRepository(session, ctx).create(
                knowledge_base_id=req.notebook_id, user_id=ctx.user_id, widget_id=widget_id
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
                claim_checks=(
                    None
                    if claim_checks is None
                    else [c.model_dump(mode="json") for c in claim_checks]
                ),
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
        reranker: Reranker,
        correlation_id: str,
        widget_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """SSE streaming variant of ``ask``: yields ``{"type":"token","content":"..."}``
        events as the LLM generates output, then a final ``{"type":"done",...}`` event
        carrying the persisted conversation/citations. No mid-stream retry — once tokens
        are flowing the client has partial output and a restart would confuse it.
        ``widget_id`` (default ``None``) is only ever passed by the embed widget's public
        controller (``app/services/embed.py``); the authenticated ``/chat/stream`` route
        never passes it, so its default path stays byte-identical to before this kwarg
        existed."""
        # P1 broad-query router (memory.md "P1 roadmap"): same gate as `ask` above —
        # off by default, zero extra DB/LLM calls when so. On a broad-query hit, zero
        # `token` events are emitted (map-reduce doesn't stream incrementally in this
        # first version) and exactly one `done` event carries the synthesized answer —
        # the same "zero tokens, one done event" shape the weak-evidence gate already
        # uses below.
        if settings.BROAD_QUERY_ENABLED:
            scope = await retrieval_service.resolve_notebook_scope(ctx, req.notebook_id)
            broad = await try_broad_query(ctx, scope, req.query, llm=llm)
            if broad is not None:
                logger.info(
                    "chat.stream_broad_query_used",
                    correlation_id=correlation_id,
                    notebook_id=str(req.notebook_id),
                    num_blocks=len(broad.hits),
                )
                conversation_id, message_id = await self._persist(
                    ctx,
                    req=req,
                    answer=broad.answer,
                    citations=broad.citations,
                    hits=broad.hits,
                    final_prompt=broad.final_prompt,
                    widget_id=widget_id,
                )
                yield {
                    "type": "done",
                    "correlation_id": correlation_id,
                    "conversation_id": str(conversation_id),
                    "message_id": str(message_id),
                    "notebook_id": str(req.notebook_id),
                    "query": req.query,
                    "answer": broad.answer,
                    "citations": [c.model_dump(mode="json") for c in broad.citations],
                    "model": llm.model,
                    "weak_evidence": False,
                }
                return

        retrieval_response = await retrieval_service.search(
            ctx,
            RetrievalSearchRequest(notebook_id=req.notebook_id, query=req.query, k=req.k),
            embedder=embedder,
            reranker=reranker,
        )
        logger.info(
            "chat.stream_context_assembled",
            correlation_id=correlation_id,
            notebook_id=str(req.notebook_id),
            num_blocks=len(retrieval_response.results),
        )
        messages = build_messages(req.query, retrieval_response.results)

        if _weak_evidence_gate_fires(retrieval_response.results):
            logger.info(
                "chat.stream_weak_evidence_gate_fired",
                correlation_id=correlation_id,
                notebook_id=str(req.notebook_id),
                top_rerank_score=retrieval_response.results[0].rerank_score,
                threshold=settings.RERANK_MIN_SCORE,
            )
            answer = _WEAK_EVIDENCE_MESSAGE
            citations: list[ResolvedCitation] = []
            conversation_id, message_id = await self._persist(
                ctx,
                req=req,
                answer=answer,
                citations=citations,
                hits=retrieval_response.results,
                final_prompt=format_prompt_for_trace(messages),
                widget_id=widget_id,
            )
            yield {
                "type": "done",
                "correlation_id": correlation_id,
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "notebook_id": str(req.notebook_id),
                "query": req.query,
                "answer": answer,
                "citations": [],
                "model": llm.model,
                "weak_evidence": True,
            }
            return

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
        claim_checks = await self._maybe_check_claims(
            ctx,
            req=req,
            answer=answer,
            blocks=retrieval_response.results,
            embedder=embedder,
            correlation_id=correlation_id,
        )
        conversation_id, message_id = await self._persist(
            ctx,
            req=req,
            answer=answer,
            citations=citations,
            hits=retrieval_response.results,
            final_prompt=format_prompt_for_trace(messages),
            widget_id=widget_id,
            claim_checks=claim_checks,
        )
        done: dict = {
            "type": "done",
            "correlation_id": correlation_id,
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "notebook_id": str(req.notebook_id),
            "query": req.query,
            "answer": answer,
            "citations": [c.model_dump(mode="json") for c in citations],
            "model": llm.model,
            "weak_evidence": False,
        }
        # Key present only when the checker ran -- flag off keeps the event byte-identical.
        if claim_checks is not None:
            done["claim_checks"] = [c.model_dump(mode="json") for c in claim_checks]
        yield done

    async def list_messages(self, ctx: TenantContext, notebook_id: uuid.UUID) -> list[MessageOut]:
        """History hydration for the chat panel (fixes the "conversation vanishes on
        navigation" bug — nothing previously read ``conversations``/``messages`` back for
        a user). Validates the notebook exists and belongs to this org first, via
        ``knowledge_service.get_notebook`` (raises ``NotebookNotFound``/
        ``NotebookAccessDenied`` -> 404/403 — the correct multi-tenant + notebook-privacy
        check), same precedent as ``submit_feedback``. Also seeds each message's
        ``my_feedback`` from the CALLING user's own prior rating only — never another
        user's rating on the same message."""
        await knowledge_service.get_notebook(ctx, notebook_id)
        async with db_mod.tenant_session(ctx.org_id) as session:
            messages = await MessageRepository(session, ctx).list_for_notebook(notebook_id)
            feedback_by_message: dict[uuid.UUID, str] = {}
            if ctx.user_id is not None:
                feedback_by_message = await FeedbackRepository(session, ctx).get_for_messages(
                    [m.id for m in messages], ctx.user_id
                )
        return [
            MessageOut.model_validate(m).model_copy(
                update={"my_feedback": feedback_by_message.get(m.id)}
            )
            for m in messages
        ]

    async def submit_feedback(
        self, ctx: TenantContext, message_id: uuid.UUID, req: FeedbackCreate
    ) -> FeedbackOut:
        """Rate an assistant message (upsert — one current rating per user per
        message). Reaches the notebook-privacy check via ``knowledge_service.
        get_notebook`` (raises ``NotebookAccessDenied``/``NotebookNotFound`` as
        appropriate) rather than reimplementing it — the exact same check
        ``list_messages`` already performs (module-boundary rule: chat reaches
        knowledge only through its service, never its repository/tables)."""
        assert ctx.user_id is not None, "submit_feedback is only reachable via get_ctx"
        async with db_mod.tenant_session(ctx.org_id) as session:
            found = await MessageRepository(session, ctx).get_with_notebook_id(message_id)
            if found is None:
                raise MessageNotFound("Message not found")
            message, notebook_id = found
            if message.role != "assistant":
                raise FeedbackOnUserMessage("Only assistant messages can be rated")
            await knowledge_service.get_notebook(ctx, notebook_id)
            feedback = await FeedbackRepository(session, ctx).upsert(
                message_id=message_id,
                user_id=ctx.user_id,
                rating=req.rating,
                reason_tags=req.reason_tags,
                comment=req.comment,
                corrected_answer=req.corrected_answer,
            )
        return FeedbackOut.model_validate(feedback)

    async def get_trace(self, ctx: TenantContext, message_id: uuid.UUID) -> MessageTraceOut:
        """F42: read-only, admin-gated at the controller (``require_admin``). Returns the
        trace verbatim from storage — never recomputed. ``hits`` validates as EITHER
        ``ContextBlock`` (the unchanged chunk/flat/hierarchical/hybrid/rerank path) or
        ``SynthesisBlock`` (P1 broad-query map-reduce) via ``_HIT_ADAPTER`` — Pydantic
        disambiguates the two since their required fields don't overlap."""
        async with db_mod.tenant_session(ctx.org_id) as session:
            trace = await TraceRepository(session, ctx).get_by_message_id(message_id)
        if trace is None:
            raise MessageTraceNotFound("Trace not found")
        return MessageTraceOut(
            id=trace.id,
            message_id=trace.message_id,
            hits=_HIT_ADAPTER.validate_python(trace.hits),
            final_prompt=trace.final_prompt,
            raw_output=trace.raw_output,
            created_at=trace.created_at,
        )

    async def get_curation_snapshot(
        self, ctx: TenantContext, message_id: uuid.UUID
    ) -> CurationSnapshot:
        """The ONLY way ``app.services.evals`` reads chat data (module-boundary rule —
        evals never imports ``chat``'s repository classes or ORM models directly).
        ``message_id`` must reference a persisted ASSISTANT message (an answer) with a
        trace; the paired question is the OTHER (``role='user'``) message in the same
        conversation, found via ``get_user_question_in_conversation`` — every
        ``/chat/ask``/``/chat/stream`` call creates a fresh conversation with exactly
        one user + one assistant message (see ``ask``'s docstring: no conversation
        reuse), so there is exactly one candidate. ``reference_contexts`` is built from
        the trace's persisted ``hits`` (each shaped like ``ContextBlock``, carrying a
        ``content`` key) — the retrieved chunk TEXT, not chunk ids, so a golden question
        stays gradable even after its source chunks are re-ingested or deleted."""
        async with db_mod.tenant_session(ctx.org_id) as session:
            found = await MessageRepository(session, ctx).get_with_notebook_id(message_id)
            if found is None:
                raise MessageNotFound("Message not found")
            message, notebook_id = found
            if message.role != "assistant":
                raise CannotCurateUserMessage(
                    "Only assistant messages can be added to the golden set"
                )
            trace = await TraceRepository(session, ctx).get_by_message_id(message_id)
            if trace is None:
                raise MessageTraceNotFound("Trace not found")
            question_message = await MessageRepository(
                session, ctx
            ).get_user_question_in_conversation(message.conversation_id)
        return CurationSnapshot(
            notebook_id=notebook_id,
            question=question_message.content if question_message is not None else "",
            reference_answer=message.content,
            reference_contexts=[hit["content"] for hit in trace.hits],
        )


chat_service = ChatService()
