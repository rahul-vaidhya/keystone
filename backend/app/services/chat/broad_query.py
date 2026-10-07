"""P1 broad-query router (memory.md "P1 roadmap: broad-query router + map-reduce").
Classifies a chat query as BROAD (an aggregate/gist-of-everything question the flat/
hybrid/rerank pipeline structurally can't answer) or SPECIFIC (a normal lookup, routed
through the existing, unchanged retrieval pipeline), and glues a BROAD query's
map-reduce result (``app.services.retrieval.mapreduce``) into the shape
``ChatService.ask``/``stream_ask`` already know how to persist via ``ChatService._persist``
— no new persistence path, no change to ``_persist``'s calling convention.

Chat-specific glue only — the map-reduce mechanics live in
``app.services.retrieval.mapreduce`` (a sibling retrieval strategy to flat/hierarchical/
hybrid, reusable by a future Notebook Overview feature); this module owns the query
classifier and the ``ResolvedCitation(citation_type="section")`` translation, both
chat-specific concerns.

Defines its OWN tiny ``[n]``-marker parser rather than importing
``chat.service.parse_citation_markers``: ``chat.service`` imports THIS module (to route a
query before falling through to its existing pipeline), so the reverse import would be
circular. The duplication is a few lines of a pure regex function — not worth a shared
utility module for.
"""

from __future__ import annotations

import re
import uuid

from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.models.chat import ResolvedCitation
from app.models.retrieval import SynthesisBlock
from app.services.retrieval.mapreduce import (
    MapReduceResult,
    collect_section_summaries,
    is_broad_query_available,
    run_map_reduce,
)
from app.services.seams import LLM, Message

logger = get_logger(__name__)

_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

_CLASSIFIER_SYSTEM_PROMPT = (
    "Classify the user's question as exactly one word: BROAD or SPECIFIC. BROAD means "
    "the question asks for an aggregate, summary, or gist across an entire document or "
    'notebook (e.g. "what\'s the gist of this", "summarize everything", "what should '
    'I be concerned about across these sources"). SPECIFIC means the question asks for '
    "a particular fact, detail, or lookup answerable from one or a few passages, "
    'including "what is X" / "explain X" questions about a single concept or term. '
    "Respond with exactly one word and nothing else: BROAD or SPECIFIC."
)


def _parse_markers(answer: str) -> list[int]:
    """Tiny local copy of ``chat.service.parse_citation_markers`` (see module docstring
    for why it isn't imported) — extracts ``[n]`` markers in order of first appearance,
    deduplicated."""
    seen: set[int] = set()
    markers: list[int] = []
    for match in _CITATION_MARKER_RE.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.add(n)
            markers.append(n)
    return markers


async def classify_query(query: str, *, llm: LLM) -> bool:
    """One cheap LLM call through the existing seam — no new model/config, deliberately
    tight/cheap prompt (a fast classification call, not a reasoning-heavy one). Returns
    True only when the model's response is exactly (case-insensitively) "BROAD"; any
    other output — including empty/malformed responses — is treated as SPECIFIC, the
    safe default that falls through to the existing, already-proven pipeline
    unchanged."""
    tokens = [
        token
        async for token in llm.stream(
            [
                Message(role="system", content=_CLASSIFIER_SYSTEM_PROMPT),
                Message(role="user", content=query),
            ]
        )
    ]
    verdict = "".join(tokens).strip().upper()
    return verdict == "BROAD"


def _resolve_synthesis_citations(
    answer: str, blocks: list[SynthesisBlock]
) -> list[ResolvedCitation]:
    """Mirrors ``chat.service.resolve_citations``'s "never fabricate" contract for the
    section path: maps the answer's ``[n]`` markers to the ``SynthesisBlock``s actually
    sent to the reduce step, silently dropping any marker outside that range (same as
    the chunk path's out-of-range handling). Pure function — every field a
    ``ResolvedCitation`` needs is already on the block (no DB re-read needed here, unlike
    the chunk path's ``get_chunks`` round trip, because a ``SynthesisBlock`` IS the
    source-of-truth text for its own citation, not a copy of a row that could have
    drifted)."""
    resolved: list[ResolvedCitation] = []
    for marker in _parse_markers(answer):
        if not (1 <= marker <= len(blocks)):
            continue
        block = blocks[marker - 1]
        resolved.append(
            ResolvedCitation(
                marker=marker,
                document_id=block.document_ids[0],
                chunk_id=None,
                char_start=None,
                char_end=None,
                content=block.content,
                citation_type="section",
                section_id=block.section_ids[0] if block.section_ids else None,
                heading=block.headings[0] if block.headings else None,
            )
        )
    return resolved


class BroadQueryResult:
    """The shape ``ChatService.ask``/``stream_ask`` need to finish the request on the
    broad-query path — mirrors what the flat path already has in hand (an answer, hits
    for the trace, and resolved citations) without needing any new fields on
    ``ChatService._persist``. Plain class, not a Pydantic model: this never crosses an
    HTTP boundary or gets serialized on its own — ``ChatResponse``/``MessageTraceOut``
    are what get serialized, both already additive."""

    __slots__ = ("answer", "hits", "citations", "final_prompt")

    def __init__(
        self,
        answer: str,
        hits: list[SynthesisBlock],
        citations: list[ResolvedCitation],
        final_prompt: str,
    ) -> None:
        self.answer = answer
        self.hits = hits
        self.citations = citations
        self.final_prompt = final_prompt


async def try_broad_query(
    ctx: TenantContext, document_ids: list[uuid.UUID], query: str, *, llm: LLM
) -> BroadQueryResult | None:
    """Attempts the broad-query map-reduce path; returns ``None`` when it must NOT run:
    the query classifies as SPECIFIC, zero section summaries exist among
    ``document_ids``, or ``document_ids`` exceeds ``BROAD_QUERY_MAX_DOCUMENTS``. Callers
    MUST check ``settings.BROAD_QUERY_ENABLED`` themselves BEFORE calling this (this
    function does not re-check it) so that the flag-off path performs ZERO extra
    work — no section-summary fetch, no classifier call — the true "byte-identical
    when off" guarantee, not merely a same-outcome one. On every ``None`` return the
    caller falls through to the existing flat/hybrid/rerank pipeline UNCHANGED; this
    function makes zero persistence calls itself, so a ``None`` return leaves no trace
    of ever having run."""
    section_summaries = await collect_section_summaries(ctx, document_ids)
    if not is_broad_query_available(document_ids, section_summaries):
        logger.info(
            "chat.broad_query_fallback_unavailable",
            org_id=str(ctx.org_id),
            document_count=len(document_ids),
            section_summary_count=len(section_summaries),
        )
        return None

    if not await classify_query(query, llm=llm):
        return None

    result: MapReduceResult = await run_map_reduce(section_summaries, query, llm=llm)
    citations = _resolve_synthesis_citations(result.answer, result.blocks)
    return BroadQueryResult(
        answer=result.answer,
        hits=result.blocks,
        citations=citations,
        final_prompt=result.final_prompt,
    )
