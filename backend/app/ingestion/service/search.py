"""``flat_vector`` retrieval (F31): the narrow entry point ``retrieval.service`` calls
instead of importing ``ingestion.repository``/``ingestion.models`` directly (module-boundary
rule — chunks/embeddings are ingestion's tables)."""

from __future__ import annotations

import uuid

from app.ingestion.repository import ChunkRepository, EmbeddingRepository
from app.ingestion.schemas import ChunkHit, ChunkRecord
from app.platform import db as db_mod
from app.platform.context import TenantContext


async def search_chunks(
    ctx: TenantContext,
    *,
    query_vector: list[float],
    document_ids: list[uuid.UUID],
    model: str,
    k: int,
) -> list[ChunkHit]:
    """All SQL lives in ``EmbeddingRepository.search_chunks``; this is pure orchestration."""
    async with db_mod.sessionmaker() as session:
        return await EmbeddingRepository(session, ctx).search_chunks(
            query_vector, document_ids, model, k
        )


async def get_chunks(ctx: TenantContext, chunk_ids: list[uuid.UUID]) -> list[ChunkRecord]:
    """F41 citation resolution's entry point — re-fetches chunk rows by id, org-scoped
    via ``ChunkRepository.get_by_ids``, so a caller (``chat.service``) can rebuild a
    citation from the source-of-truth row rather than trusting an earlier in-request copy."""
    async with db_mod.sessionmaker() as session:
        chunks = await ChunkRepository(session, ctx).get_by_ids(chunk_ids)
    return [
        ChunkRecord(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
        )
        for chunk in chunks
    ]
