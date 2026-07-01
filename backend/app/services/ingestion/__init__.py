"""F20 parsing + F21 structuring + F22 embedding stages — the staged ingestion pipeline
(architecture.md "Ingestion pipeline"). Calls into ``documents.service`` for every
document-table mutation (module boundary rule: a module reaches another module only
through its service, never its repository); reaches the ``Parser``/``Embedder`` seams and
the object store (not a seam — called directly) to do the actual work.

This package is a structural split of what used to be one flat ``service.py`` (444 lines
mixing the parsing/structuring/embedding/search stages) — the refactor that introduced
this split made ZERO logic changes. Each stage's algorithm lives in its own module
(``parsing.py``/``structuring.py``/``embedding.py``/``search.py``); ``IngestionService``
here is pure delegation, so ``ingestion_service.run_parsing_stage(...)`` etc. resolve
exactly as before.
"""

from __future__ import annotations

import uuid

from app.platform.context import TenantContext
from app.platform.queue import JobQueue
from app.platform.seams import Embedder, Parser
from app.platform.storage import ObjectStore
from app.schemas.documents import DocumentOut
from app.schemas.ingestion import ChunkHit, ChunkRecord
from app.services.ingestion import embedding as _embedding
from app.services.ingestion import parsing as _parsing
from app.services.ingestion import search as _search
from app.services.ingestion import structuring as _structuring


class IngestionService:
    async def enqueue_pipeline(
        self, ctx: TenantContext, document_id: uuid.UUID, *, job_queue: JobQueue
    ) -> None:
        """F24: kick off the pipeline by enqueueing the parsing stage's job. Called by
        ``documents`` module's upload router (never by ``documents.service`` directly —
        ``ingestion`` already imports ``documents.service``, so the reverse import would
        be circular; the router is the composition point, same module-boundary rule
        applied at the HTTP edge instead of service-to-service)."""
        await job_queue.enqueue(
            "run_parsing_stage_job",
            job_id=f"ingestion:parsing:{document_id}",
            org_id=str(ctx.org_id),
            document_id=str(document_id),
        )

    async def run_parsing_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        parser: Parser,
        object_store: ObjectStore,
    ) -> DocumentOut:
        return await _parsing.run_parsing_stage(
            ctx, document_id, parser=parser, object_store=object_store
        )

    async def run_structuring_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        object_store: ObjectStore,
    ) -> DocumentOut:
        return await _structuring.run_structuring_stage(ctx, document_id, object_store=object_store)

    async def run_embedding_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        embedder: Embedder,
    ) -> DocumentOut:
        return await _embedding.run_embedding_stage(ctx, document_id, embedder=embedder)

    async def search_chunks(
        self,
        ctx: TenantContext,
        *,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        k: int,
    ) -> list[ChunkHit]:
        return await _search.search_chunks(
            ctx, query_vector=query_vector, document_ids=document_ids, model=model, k=k
        )

    async def get_chunks(self, ctx: TenantContext, chunk_ids: list[uuid.UUID]) -> list[ChunkRecord]:
        return await _search.get_chunks(ctx, chunk_ids)


ingestion_service = IngestionService()
