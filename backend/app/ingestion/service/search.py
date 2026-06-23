"""``flat_vector`` retrieval (F31): the narrow entry point ``retrieval.service`` calls
instead of importing ``ingestion.repository``/``ingestion.models`` directly (module-boundary
rule — chunks/embeddings are ingestion's tables)."""

from __future__ import annotations

import uuid

from app.ingestion.repository import EmbeddingRepository
from app.ingestion.schemas import ChunkHit
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
