"""F22 embedding stage: embed chunks; upsert ``embeddings(owner_type='chunk')``."""

from __future__ import annotations

import uuid

from app.config import db as db_mod
from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut, DocumentStatus
from app.services.documents import documents_service
from app.services.ingestion.repository import ChunkRepository, EmbeddingRepository
from app.services.seams import Embedder

logger = get_logger(__name__)


async def run_embedding_stage(
    ctx: TenantContext,
    document_id: uuid.UUID,
    *,
    embedder: Embedder,
) -> DocumentOut:
    """EMBEDDING -> READY, or -> FAILED with failed_stage=EMBEDDING.

    Idempotent and resumable, same shape as the earlier stages: a document already past
    embedding (READY) is returned unchanged; a document stuck in EMBEDDING or previously
    FAILED at this stage is re-embedded from scratch. Unlike structuring, re-running this
    stage does NOT delete-then-rebuild — it upserts on the embeddings table's
    ``unique(owner_type, owner_id, model)`` constraint, so a re-embed of an unchanged
    chunk under the same model updates the same row instead of inserting a duplicate.
    """
    document = await documents_service.begin_embedding(ctx, document_id)
    if document.status != DocumentStatus.EMBEDDING:
        return document

    try:
        async with db_mod.sessionmaker() as session:
            chunks = await ChunkRepository(session, ctx).list_for_document(document_id)
        vectors = await embedder.embed([chunk.content for chunk in chunks])
    except Exception as exc:  # the only seam call here — record, never swallow
        logger.warning(
            "ingestion.embedding_failed",
            document_id=str(document_id),
            org_id=str(ctx.org_id),
            error=str(exc),
        )
        return await documents_service.fail_stage(
            ctx,
            document_id,
            failed_stage=DocumentStatus.EMBEDDING.value,
            error_detail=str(exc),
        )

    rows = [
        {
            "owner_id": chunk.id,
            "model": embedder.model,
            "dim": embedder.dim,
            "embedding": vector,
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    async with db_mod.sessionmaker() as session, session.begin():
        await EmbeddingRepository(session, ctx).upsert_chunk_embeddings(document_id, rows)

    return await documents_service.complete_embedding(ctx, document_id)
