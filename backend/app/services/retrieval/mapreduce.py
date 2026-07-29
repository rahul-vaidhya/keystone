"""Map-reduce over section summaries — the broad-query retrieval strategy, a 4th sibling
alongside flat/hierarchical/hybrid (``app.services.retrieval.{service,fusion,
permissions}``). Built for the P1 "gist of everything" / aggregate-question gap
(memory.md "P1 roadmap"): flat/hybrid/rerank top-k chunk search structurally cannot
synthesize across many/all sources — that's a query-focused-summarization task, not a
lookup task.

Reusable by BOTH the chat broad-query router (``app.services.chat.broad_query``) and a
future Notebook Overview feature — every public function here takes a document-id scope
plus a ``purpose`` string (a user's actual question for chat, or a fixed instruction like
"summarize the key points across these sources" for an Overview) rather than anything
chat-specific. Reaches ``sections`` ONLY through
``ingestion_service.list_section_summaries`` (module-boundary rule — sections/chunks are
ingestion's tables, never imported directly here).

Two-stage pipeline, run entirely inline in the current request — explicitly NOT a new
arq/background-job system:

1. **Map** — one LLM call PER SECTION, parallelized via ``asyncio.gather``, extracting
   whatever in that section's V2 enrichment summary (``sections.summary`` — already-
   built, dormant infra, see ``app.services.ingestion.enrichment``) is relevant to
   ``purpose``. Operates on the summary alone: a section's summary IS the compressed
   representation enrichment already built for exactly this kind of cross-section
   reasoning, so re-fetching/re-embedding the underlying chunk text would defeat the
   point of reusing it (``ChunkRepository.list_for_sections`` exists for a future
   variant that needs more than the summary, but is NOT called from here).
2. **Reduce** — one final LLM call synthesizing every non-empty map extract into a
   single answer, citing extracts by number (``[n]``) — same numbering convention
   ``chat.service.build_messages``/``ContextBlock`` use for the flat path. The citation
   markers resolve back to ``SynthesisBlock``s carrying section-level provenance via
   ``build_synthesis_blocks``.

**Fallback contract** — callers MUST check ``is_broad_query_available`` themselves
before calling ``run_map_reduce``; this module never checks it internally. The caller
owns the INFO log + fallback decision, matching ``HIERARCHICAL_RETRIEVAL_ENABLED``'s
existing "log INFO, degrade, never raise" shape in ``services/retrieval/service.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.retrieval import SectionSummaryHit, SynthesisBlock
from app.services.ingestion import ingestion_service
from app.services.seams import LLM, Message

_NOT_RELEVANT = "NOT_RELEVANT"

_MAP_SYSTEM_PROMPT = (
    "You extract information relevant to a question from one section of a document, "
    "given only that section's summary. Respond with a short (1-3 sentence) extract of "
    "whatever in the summary is relevant. If nothing in the summary is relevant, "
    f"respond with exactly one word and nothing else: {_NOT_RELEVANT}."
)

_REDUCE_SYSTEM_PROMPT = (
    "You synthesize one answer from several numbered section extracts below. Cite the "
    "extracts you used by their number in square brackets, e.g. [1]. If none of the "
    "extracts are relevant, respond with exactly this sentence and nothing else: "
    '"I don\'t have that in the provided sources." Never use outside or prior '
    "knowledge."
)


@dataclass
class MapReduceResult:
    """The reduce step's full output: the synthesized answer text, the
    ``SynthesisBlock``s it can cite from (section-level provenance for a
    ``citation_type="section"`` ``ResolvedCitation``), and the exact reduce-step prompt
    (for trace persistence, mirroring ``chat.service.format_prompt_for_trace``)."""

    answer: str
    blocks: list[SynthesisBlock]
    final_prompt: str


async def collect_section_summaries(
    ctx: TenantContext, document_ids: list[uuid.UUID]
) -> list[SectionSummaryHit]:
    """Every section across ``document_ids`` carrying a non-null V2 enrichment summary —
    reaches ``sections`` only through ``ingestion_service.list_section_summaries``
    (module-boundary rule). An empty result means "no enrichment has run for this scope
    yet" — the signal ``is_broad_query_available`` checks for the fallback."""
    return await ingestion_service.list_section_summaries(ctx, document_ids)


def is_broad_query_available(
    document_ids: list[uuid.UUID], section_summaries: list[SectionSummaryHit]
) -> bool:
    """Pure function — the fallback gate every caller MUST check before invoking
    ``run_map_reduce``. Two independent trip conditions, per the locked design: zero
    section summaries among the scope documents (enrichment hasn't run), or the scope's
    document count exceeding ``BROAD_QUERY_MAX_DOCUMENTS`` (a safety cap on how much
    inline map-reduce work one request will do). This function itself never logs (it has
    no ``ctx``/correlation id for scoped logging) — the caller logs INFO on a ``False``
    return, mirroring ``HIERARCHICAL_RETRIEVAL_ENABLED``'s existing fallback shape."""
    if not document_ids or len(document_ids) > settings.BROAD_QUERY_MAX_DOCUMENTS:
        return False
    return bool(section_summaries)


async def _map_section(section: SectionSummaryHit, purpose: str, *, llm: LLM) -> str:
    heading = section.heading or "(untitled)"
    user_content = f"Section heading: {heading}\nSection summary: {section.summary}\n\n{purpose}"
    tokens = [
        token
        async for token in llm.stream(
            [
                Message(role="system", content=_MAP_SYSTEM_PROMPT),
                Message(role="user", content=user_content),
            ]
        )
    ]
    return "".join(tokens).strip()


async def _run_map_step(
    sections: list[SectionSummaryHit], purpose: str, *, llm: LLM
) -> list[tuple[SectionSummaryHit, str]]:
    """One LLM call PER SECTION, parallelized via ``asyncio.gather`` — inline in the
    request, never a background job. Drops sections whose extract is empty or the
    literal ``NOT_RELEVANT`` sentinel — the reduce step never sees an irrelevant
    section."""
    if not sections:
        return []
    extracts = await asyncio.gather(*(_map_section(s, purpose, llm=llm) for s in sections))
    return [
        (section, extract)
        for section, extract in zip(sections, extracts, strict=True)
        if extract and extract.strip().upper() != _NOT_RELEVANT
    ]


def _build_reduce_messages(
    purpose: str, relevant: list[tuple[SectionSummaryHit, str]]
) -> list[Message]:
    if relevant:
        context_text = "\n\n".join(
            f"[{i}] {extract}" for i, (_section, extract) in enumerate(relevant, start=1)
        )
    else:
        context_text = "(no section extracts were relevant)"
    user_content = f"{context_text}\n\n{purpose}"
    return [
        Message(role="system", content=_REDUCE_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


def _format_prompt_for_trace(messages: list[Message]) -> str:
    """Mirrors ``chat.service.format_prompt_for_trace`` exactly, deliberately NOT
    imported from there: ``chat.service`` imports ``chat.broad_query``, which imports
    THIS module, so importing back from ``chat.service`` here would be circular. A
    one-line pure formatter is not worth restructuring either module to share."""
    return "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages)


def build_synthesis_blocks(relevant: list[tuple[SectionSummaryHit, str]]) -> list[SynthesisBlock]:
    """Pure function — numbers the map step's surviving (section, extract) pairs into
    ``SynthesisBlock``s, the same ``[n]``-numbering convention
    ``chat.service.build_messages``/``ContextBlock`` use for the flat path, so the
    reduce step's own ``[n]`` citations resolve back to these blocks 1:1."""
    return [
        SynthesisBlock(
            index=i,
            content=extract,
            section_ids=[section.section_id],
            headings=[section.heading],
            document_ids=[section.document_id],
        )
        for i, (section, extract) in enumerate(relevant, start=1)
    ]


async def run_map_reduce(
    section_summaries: list[SectionSummaryHit], purpose: str, *, llm: LLM
) -> MapReduceResult:
    """The full map-reduce pipeline. Callers MUST have already confirmed
    ``is_broad_query_available`` — this function does not re-check the fallback
    conditions itself, so calling it with an empty ``section_summaries`` list is a
    caller bug, not a fallback path (it would simply reduce over zero extracts and let
    the reduce LLM's own grounding instruction produce its refusal sentence, same shape
    as ``chat.service.build_messages``'s empty-context path)."""
    relevant = await _run_map_step(section_summaries, purpose, llm=llm)
    messages = _build_reduce_messages(purpose, relevant)
    tokens = [token async for token in llm.stream(messages)]
    answer = "".join(tokens).strip()
    return MapReduceResult(
        answer=answer,
        blocks=build_synthesis_blocks(relevant),
        final_prompt=_format_prompt_for_trace(messages),
    )
