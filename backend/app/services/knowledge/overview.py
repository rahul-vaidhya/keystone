"""Notebook Overview (P1 roadmap, memory.md "P1 roadmap: broad-query router + map-reduce,
contextual retrieval, Notebook Overview" — feature 3 of 3): an on-demand, cached "gist of
everything" artifact for a notebook, built by reusing ``app.services.retrieval.mapreduce``
— the SAME generic map-reduce mechanism feature 1 (``app.services.chat.broad_query``)
already wired up for a live chat answer, but persisted standalone (one row per notebook,
``NotebookOverviewRepository.upsert``) rather than answered per chat message.

On-demand only — never auto-generated on notebook creation or on every document
attach/detach. ``notebooks.py``'s ``attach_document``/``detach_document`` call
``mark_stale`` (deferred import there, to avoid a circular import: this module imports
``notebooks.py`` at load time for ``fetch_visible``, so the reverse import must be
lazy) to flag an existing overview out of date; this module never deletes or
auto-regenerates a stale row, only flags it.

Defines its OWN tiny citation-resolution helper rather than importing
``chat.broad_query._resolve_synthesis_citations``: ``app.services.chat`` imports
``app.services.knowledge`` (``knowledge_service.get_notebook`` etc.), so importing
back from chat here would be circular — same reasoning ``chat.broad_query``'s own
module docstring gives for duplicating its citation-marker regex from
``chat.service`` rather than sharing it.

Deliberately does NOT apply Access-Role tag-based document filtering
(``resolve_allowed_documents``/``resolve_notebook_scope``): the Overview is ONE cached
artifact shared by every notebook member, not a personalized per-viewer answer, so
filtering by the GENERATING user's own document visibility would make the cached
result inconsistent across viewers (or worse, leak content from documents a later,
less-privileged reader can't otherwise see). Notebook-level access (``fetch_visible``)
is the only gate — the same scope every attached document already gets via being in
the notebook the caller can see.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import db as db_mod
from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.chat import ResolvedCitation
from app.models.knowledge import NotebookOverview, NotebookOverviewOut
from app.models.retrieval import SynthesisBlock
from app.services.base import BaseRepository
from app.services.knowledge.exceptions import OverviewNotFound, OverviewUnavailable
from app.services.knowledge.notebooks import NotebookDocumentRepository, fetch_visible
from app.services.seams import LLM

# NOTE: `app.services.retrieval.mapreduce`'s helpers (`collect_section_summaries`,
# `is_broad_query_available`, `run_map_reduce`) are imported INSIDE `generate_overview`
# below, not here at module level. Importing that submodule triggers
# `app.services.retrieval`'s package `__init__.py`, which imports `app.services.retrieval.
# service`, which imports `knowledge_service` from THIS package — a real circular import
# (knowledge -> retrieval -> knowledge) if done at module load time, since `retrieval`
# legitimately depends on `knowledge` (the reverse dependency direction from every other
# module in this codebase). Deferring to call time breaks the cycle: by the time
# `generate_overview` is actually invoked, `app.services.knowledge`'s own package
# `__init__.py` has long since finished executing.

logger = get_logger(__name__)

_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

_OVERVIEW_PURPOSE = (
    "Produce a comprehensive overview of what this notebook's documents cover: the main "
    "topics, key points, and anything a new reader should know before diving in. Write "
    "it as a well-organized standalone summary, not an answer to a specific question."
)


# ---- repository ----
class NotebookOverviewRepository(BaseRepository[NotebookOverview]):
    model = NotebookOverview

    async def get_by_notebook(self, notebook_id: uuid.UUID) -> NotebookOverview | None:
        stmt = self._scoped().where(NotebookOverview.notebook_id == notebook_id)
        return await self._db.scalar(stmt)

    async def upsert(
        self,
        *,
        notebook_id: uuid.UUID,
        content: str,
        citations: list[dict],
        generated_by: uuid.UUID | None,
        source_document_count: int,
    ) -> NotebookOverview:
        """Upsert on ``unique(notebook_id)`` — one current overview per notebook, not an
        accumulating history. Regenerating overwrites the same row in place, clearing
        ``stale`` and refreshing ``generated_at``/``source_document_count``."""
        now = datetime.now(UTC)
        stmt = (
            pg_insert(NotebookOverview)
            .values(
                org_id=self._ctx.org_id,
                notebook_id=notebook_id,
                content=content,
                citations=citations,
                generated_at=now,
                generated_by=generated_by,
                source_document_count=source_document_count,
                stale=False,
            )
            .on_conflict_do_update(
                index_elements=["notebook_id"],
                set_={
                    "content": content,
                    "citations": citations,
                    "generated_at": now,
                    "generated_by": generated_by,
                    "source_document_count": source_document_count,
                    "stale": False,
                },
            )
            .returning(NotebookOverview)
        )
        result = await self._db.execute(stmt)
        await self._db.flush()
        return result.scalar_one()

    async def mark_stale(self, notebook_id: uuid.UUID) -> None:
        """No-op if no overview row exists yet for this notebook — marking staleness on
        an overview that's never been generated is meaningless, not an error."""
        stmt = self._scoped().where(NotebookOverview.notebook_id == notebook_id)
        overview = await self._db.scalar(stmt)
        if overview is not None:
            overview.stale = True
            await self._db.flush()


# ---- service ----


def _parse_markers(answer: str) -> list[int]:
    """Tiny local copy of ``chat.broad_query._parse_markers`` (see module docstring for
    why it isn't imported) — extracts ``[n]`` markers in order of first appearance,
    deduplicated."""
    seen: set[int] = set()
    markers: list[int] = []
    for match in _CITATION_MARKER_RE.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.add(n)
            markers.append(n)
    return markers


def _resolve_overview_citations(
    answer: str, blocks: list[SynthesisBlock]
) -> list[ResolvedCitation]:
    """Mirrors ``chat.broad_query._resolve_synthesis_citations`` exactly (duplicated,
    not imported — see module docstring): maps the answer's ``[n]`` markers to the
    ``SynthesisBlock``s actually sent to the reduce step, silently dropping any marker
    outside that range (never fabricate)."""
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


async def generate_overview(
    ctx: TenantContext, notebook_id: uuid.UUID, *, llm: LLM
) -> NotebookOverviewOut:
    """Generate (or regenerate) the notebook's Overview. Refuses via
    ``OverviewUnavailable`` when: the feature is disabled, the notebook's documents carry
    zero V2 enrichment section summaries (enrichment hasn't run yet), or the notebook's
    document count exceeds ``settings.BROAD_QUERY_MAX_DOCUMENTS`` — reusing
    ``is_broad_query_available``, the SAME fallback gate feature 1's chat broad-query
    router checks, so the two features never silently disagree on when map-reduce is
    viable. Unlike that router's invisible fallback, this is a user-initiated action with
    nothing to silently substitute, so it surfaces as a clear, typed 4xx instead."""
    from app.services.retrieval.mapreduce import (
        collect_section_summaries,
        is_broad_query_available,
        run_map_reduce,
    )

    if not settings.NOTEBOOK_OVERVIEW_ENABLED:
        raise OverviewUnavailable("Notebook Overview is not enabled")

    async with db_mod.tenant_session(ctx.org_id) as session:
        await fetch_visible(session, ctx, notebook_id)
        document_ids = await NotebookDocumentRepository(session, ctx).list_document_ids(notebook_id)

    section_summaries = await collect_section_summaries(ctx, document_ids)
    if not is_broad_query_available(document_ids, section_summaries):
        logger.info(
            "knowledge.overview_unavailable",
            org_id=str(ctx.org_id),
            notebook_id=str(notebook_id),
            document_count=len(document_ids),
            section_summary_count=len(section_summaries),
        )
        raise OverviewUnavailable(
            "Can't generate an overview yet — this notebook has no enriched sections, "
            "or has too many documents attached."
        )

    result = await run_map_reduce(section_summaries, _OVERVIEW_PURPOSE, llm=llm)
    citations = _resolve_overview_citations(result.answer, result.blocks)

    async with db_mod.tenant_session(ctx.org_id) as session:
        row = await NotebookOverviewRepository(session, ctx).upsert(
            notebook_id=notebook_id,
            content=result.answer,
            citations=[c.model_dump(mode="json") for c in citations],
            generated_by=ctx.user_id,
            source_document_count=len(document_ids),
        )
        out = NotebookOverviewOut.model_validate(row)
    return out


async def get_overview(ctx: TenantContext, notebook_id: uuid.UUID) -> NotebookOverviewOut:
    """Fetch the cached overview. Available regardless of
    ``settings.NOTEBOOK_OVERVIEW_ENABLED`` — turning the flag off blocks new generation,
    it doesn't retroactively hide an already-generated artifact (same philosophy as
    ``RERANKER_ENABLED`` off not erasing already-stored ``rerank_score`` values)."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        await fetch_visible(session, ctx, notebook_id)
        row = await NotebookOverviewRepository(session, ctx).get_by_notebook(notebook_id)
        if row is None:
            raise OverviewNotFound("No overview has been generated for this notebook yet")
        return NotebookOverviewOut.model_validate(row)


async def mark_stale(ctx: TenantContext, notebook_id: uuid.UUID) -> None:
    """Called by ``notebooks.py``'s ``attach_document``/``detach_document`` (via a
    deferred import there) whenever the notebook's document set changes. Never deletes
    or regenerates the cached overview — only flags it, so the frontend can show a
    "this may be out of date, regenerate?" banner."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        await NotebookOverviewRepository(session, ctx).mark_stale(notebook_id)
