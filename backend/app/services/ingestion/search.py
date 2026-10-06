"""``flat_vector`` retrieval (F31): the narrow entry point ``retrieval.service`` calls
instead of importing ``ingestion.repository``/``ingestion.models`` directly (module-boundary
rule — chunks/embeddings are ingestion's tables)."""

from __future__ import annotations

import re
import uuid

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.ingestion import ChunkHit, ChunkRecord, SectionHit, SparseIndexChunk
from app.models.retrieval import SectionSummaryHit
from app.services.ingestion.repository import (
    ChunkRepository,
    EmbeddingRepository,
    SectionRepository,
)


async def search_chunks(
    ctx: TenantContext,
    *,
    query_vector: list[float],
    document_ids: list[uuid.UUID],
    model: str,
    k: int,
    section_ids: list[uuid.UUID] | None = None,
) -> list[ChunkHit]:
    """All SQL lives in ``EmbeddingRepository.search_chunks``; this is pure orchestration."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await EmbeddingRepository(session, ctx).search_chunks(
            query_vector, document_ids, model, k, section_ids=section_ids
        )


async def search_chunks_lexical(
    ctx: TenantContext,
    *,
    query: str,
    document_ids: list[uuid.UUID],
    k: int,
    section_ids: list[uuid.UUID] | None = None,
) -> list[ChunkHit]:
    """Hybrid search's lexical candidate path — all SQL lives in
    ``ChunkRepository.search_chunks_lexical``; this is pure orchestration (mirrors
    ``search_chunks`` above)."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await ChunkRepository(session, ctx).search_chunks_lexical(
            query, document_ids, k, section_ids=section_ids
        )


async def list_chunks_for_sparse_index(
    ctx: TenantContext, document_ids: list[uuid.UUID]
) -> list[SparseIndexChunk]:
    """The in-house sparse index's corpus (``retrieval.sparse_channel``) — all SQL lives
    in ``ChunkRepository.list_for_sparse_index``; this is pure orchestration."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await ChunkRepository(session, ctx).list_for_sparse_index(document_ids)


async def chunk_fingerprint(ctx: TenantContext, document_ids: list[uuid.UUID]) -> tuple[int, str]:
    """Cache-validity fingerprint for the sparse index — all SQL lives in
    ``ChunkRepository.fingerprint_for_documents``."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await ChunkRepository(session, ctx).fingerprint_for_documents(document_ids)


async def search_sections(
    ctx: TenantContext,
    *,
    query_vector: list[float],
    document_ids: list[uuid.UUID],
    model: str,
    s: int,
) -> list[SectionHit]:
    """V2 hierarchical retrieval's coarse pass: all SQL lives in
    ``EmbeddingRepository.search_sections``; this is pure orchestration."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        return await EmbeddingRepository(session, ctx).search_sections(
            query_vector, document_ids, model, s
        )


async def list_section_summaries(
    ctx: TenantContext, document_ids: list[uuid.UUID]
) -> list[SectionSummaryHit]:
    """P1 broad-query map-reduce's input (``app.services.retrieval.mapreduce``): every
    section across ``document_ids`` carrying a non-null V2 enrichment summary
    (``sections.summary``) — all SQL lives in ``SectionRepository.list_for_documents``;
    this is pure orchestration (mirrors ``search_sections`` above). Sections without a
    summary (enrichment hasn't run, or failed, for that section) are silently excluded —
    an empty result signals "no enrichment for this scope yet", the broad-query
    fallback trigger the caller (``is_broad_query_available``) checks for."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        sections = await SectionRepository(session, ctx).list_for_documents(document_ids)
    return [
        SectionSummaryHit(
            section_id=section.id,
            document_id=section.document_id,
            heading=section.heading,
            summary=section.summary,
            topics=section.topics,
        )
        for section in sections
        if section.summary
    ]


_PAGE_MARKER_RE = re.compile(r"^#{1,6}[ \t]*Page[ \t]+(\d+)[ \t]*$", re.MULTILINE)


def derive_citation_pages(
    content: str,
    *,
    preceding_marker_text: str | None,
    section_pages: tuple[int | None, int | None],
    document_max_page: int | None,
) -> tuple[int | None, int | None]:
    """Pure: the page range a citation should display, never fabricated.

    1. Parser page markers (``### Page N`` lines, one per PDF page) are the most precise
       signal: the chunk starts on the page of a marker at its very beginning, else the
       last marker before it in the document (``preceding_marker_text``); it ends on the
       last marker inside it (or the start page).
    2. Otherwise the owning section's range — unless it is the WHOLE-document range
       (``1..document_max_page`` spanning >1 page), which says nothing about where the
       text is; then ``(None, None)`` so the UI hides the page line."""
    inside = list(_PAGE_MARKER_RE.finditer(content))
    start_page: int | None = None
    if inside and not content[: inside[0].start()].strip():
        start_page = int(inside[0].group(1))
    elif preceding_marker_text:
        before = _PAGE_MARKER_RE.findall(preceding_marker_text)
        if before:
            start_page = int(before[-1])
    if start_page is None and inside:
        # Text before the first in-chunk marker sits on the page preceding it.
        start_page = max(int(inside[0].group(1)) - 1, 1)
    if start_page is not None:
        end_page = int(inside[-1].group(1)) if inside else start_page
        return start_page, max(end_page, start_page)

    page_start, page_end = section_pages
    if (
        page_start == 1
        and page_end is not None
        and page_end > 1
        and document_max_page is not None
        and page_end >= document_max_page
    ):
        return None, None
    return page_start, page_end


async def get_chunks(ctx: TenantContext, chunk_ids: list[uuid.UUID]) -> list[ChunkRecord]:
    """F41 citation resolution's entry point — re-fetches chunk rows by id, org-scoped
    via ``ChunkRepository.get_by_ids``, so a caller (``chat.service``) can rebuild a
    citation from the source-of-truth row rather than trusting an earlier in-request copy.
    Also carries a human-readable page range (``derive_citation_pages``: parser page
    markers first, else the owning section's range, ``None`` when unknown or only the
    whole-document range is known)."""
    records: list[ChunkRecord] = []
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = ChunkRepository(session, ctx)
        rows = await repo.get_by_ids(chunk_ids)
        max_pages: dict[uuid.UUID, int | None] = {}
        for chunk, page_start, page_end in rows:
            preceding = None
            if not _PAGE_MARKER_RE.match(chunk.content.lstrip()):
                preceding = await repo.latest_page_marker_chunk_before(
                    chunk.document_id, chunk.char_start
                )
            if chunk.document_id not in max_pages:
                max_pages[chunk.document_id] = await repo.document_max_page(chunk.document_id)
            derived_start, derived_end = derive_citation_pages(
                chunk.content,
                preceding_marker_text=preceding,
                section_pages=(page_start, page_end),
                document_max_page=max_pages[chunk.document_id],
            )
            records.append(
                ChunkRecord(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    content=chunk.content,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    page_start=derived_start,
                    page_end=derived_end,
                )
            )
    return records
