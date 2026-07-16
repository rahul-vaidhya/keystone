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
from typing import TYPE_CHECKING

from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut, DocumentStatus
from app.models.ingestion import ChunkHit, ChunkRecord, EnrichmentBackfillResult, SectionHit
from app.services.documents import documents_service
from app.services.ingestion import embedding as _embedding
from app.services.ingestion import enrichment as _enrichment
from app.services.ingestion import parsing as _parsing
from app.services.ingestion import search as _search
from app.services.ingestion import structuring as _structuring
from app.services.queue import JobQueue
from app.services.seams import Embedder, Parser
from app.services.storage import ObjectStore

if TYPE_CHECKING:
    from app.services.seams import LLM

logger = get_logger(__name__)


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
        llm: LLM | None = None,
    ) -> DocumentOut:
        return await _structuring.run_structuring_stage(
            ctx, document_id, object_store=object_store, llm=llm
        )

    async def run_embedding_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        embedder: Embedder,
    ) -> DocumentOut:
        return await _embedding.run_embedding_stage(ctx, document_id, embedder=embedder)

    async def run_enrichment_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        llm: LLM,
        embedder: Embedder,
        object_store,
    ) -> DocumentOut:
        return await _enrichment.run_enrichment_stage(
            ctx, document_id, llm=llm, embedder=embedder, object_store=object_store
        )

    async def search_chunks(
        self,
        ctx: TenantContext,
        *,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        k: int,
        section_ids: list[uuid.UUID] | None = None,
    ) -> list[ChunkHit]:
        return await _search.search_chunks(
            ctx,
            query_vector=query_vector,
            document_ids=document_ids,
            model=model,
            k=k,
            section_ids=section_ids,
        )

    async def search_sections(
        self,
        ctx: TenantContext,
        *,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        s: int,
    ) -> list[SectionHit]:
        return await _search.search_sections(
            ctx, query_vector=query_vector, document_ids=document_ids, model=model, s=s
        )

    async def get_chunks(self, ctx: TenantContext, chunk_ids: list[uuid.UUID]) -> list[ChunkRecord]:
        return await _search.get_chunks(ctx, chunk_ids)

    async def run_enrichment_backfill(
        self,
        ctx: TenantContext,
        *,
        llm: LLM,
        embedder: Embedder,
        object_store: ObjectStore,
    ) -> EnrichmentBackfillResult:
        """Org-wide enrichment backfill (admin-gated) for documents that reached READY
        before ENRICHMENT_ENABLED existed — the only prior entry point was per-document.
        Sequential (not concurrent) to keep LLM call volume predictable and avoid
        hammering the seam; reuses the existing idempotent ``run_enrichment_stage`` per
        document, so re-running the backfill is safe. Lists documents via
        ``documents_service.list_documents`` (module-boundary rule: never a repository
        import here) and filters to READY client-side — a status filter isn't a shared
        concern worth adding to the general-purpose accessor."""
        docs = await documents_service.list_documents(ctx)
        ready_docs = [d for d in docs if d.status == DocumentStatus.READY]
        enriched = 0
        failed = 0
        for doc in ready_docs:
            try:
                await self.run_enrichment_stage(
                    ctx, doc.id, llm=llm, embedder=embedder, object_store=object_store
                )
                enriched += 1
            except Exception as exc:
                logger.warning(
                    "ingestion.enrichment_backfill_document_failed",
                    document_id=str(doc.id),
                    org_id=str(ctx.org_id),
                    error=str(exc),
                )
                failed += 1
        skipped = len(docs) - len(ready_docs)
        return EnrichmentBackfillResult(enriched=enriched, skipped=skipped, failed=failed)


ingestion_service = IngestionService()
