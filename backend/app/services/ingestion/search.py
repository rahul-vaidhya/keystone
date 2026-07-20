"""``flat_vector`` retrieval (F31): the narrow entry point ``retrieval.service`` calls
instead of importing ``ingestion.repository``/``ingestion.models`` directly (module-boundary
rule — chunks/embeddings are ingestion's tables)."""

from __future__ import annotations

import uuid

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.ingestion import ChunkHit, ChunkRecord, SectionHit
from app.services.ingestion.repository import ChunkRepository, EmbeddingRepository


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


async def get_chunks(ctx: TenantContext, chunk_ids: list[uuid.UUID]) -> list[ChunkRecord]:
    """F41 citation resolution's entry point — re-fetches chunk rows by id, org-scoped
    via ``ChunkRepository.get_by_ids``, so a caller (``chat.service``) can rebuild a
    citation from the source-of-truth row rather than trusting an earlier in-request copy.
    Also carries the owning section's page range (``page_start``/``page_end``, both
    ``None`` when the chunk has no section or the section has no page info) for
    human-readable citation display alongside the char offsets."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        rows = await ChunkRepository(session, ctx).get_by_ids(chunk_ids)
    return [
        ChunkRecord(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            page_start=page_start,
            page_end=page_end,
        )
        for chunk, page_start, page_end in rows
    ]
