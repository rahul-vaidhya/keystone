"""Retrieval use cases — the MVP ``flat_vector`` path (architecture.md "Retrieval
pipeline"). Owns no table; reaches other modules only through their ``service``
(module-boundary rule): ``knowledge.service`` for notebook membership, ``documents.service``
for the allowed-documents hook, ``ingestion.service`` for the actual kNN search.
"""

from __future__ import annotations

import uuid

from app.platform.context import TenantContext
from app.platform.seams import Embedder
from app.schemas.ingestion import ChunkHit
from app.schemas.retrieval import ContextBlock, RetrievalSearchRequest, RetrievalSearchResponse
from app.services.documents import documents_service
from app.services.ingestion import ingestion_service
from app.services.knowledge import knowledge_service


async def resolve_allowed_documents(ctx: TenantContext) -> list[uuid.UUID]:
    """The single seam where V2 groups/grants permission logic slots in (architecture.md).
    MVP: every document in the org is allowed — no groups/grants exist yet."""
    docs = await documents_service.list_documents(ctx)
    return [d.id for d in docs]


def assemble_context(query: str, hits: list[ChunkHit]) -> RetrievalSearchResponse:
    """Pure function — numbers hits into ``ContextBlock``s with source refs (document_id,
    chunk_id, char offsets, distance). This is the SHAPE F40 (chat) will consume; citation
    mapping itself is F41 — not built here. ``distance`` was already computed by
    ``EmbeddingRepository.search_chunks`` (F31) and is surfaced here unchanged — F40 needs
    it for its retrieval-quality logging ("chunk_ids + distances if available"); this is
    additive only, no new computation."""
    results = [
        ContextBlock(
            index=position,
            document_id=hit.document_id,
            chunk_id=hit.chunk_id,
            char_start=hit.char_start,
            char_end=hit.char_end,
            content=hit.content,
            distance=hit.distance,
        )
        for position, hit in enumerate(hits, start=1)
    ]
    return RetrievalSearchResponse(query=query, results=results)


class RetrievalService:
    async def search(
        self,
        ctx: TenantContext,
        req: RetrievalSearchRequest,
        *,
        embedder: Embedder,
    ) -> RetrievalSearchResponse:
        """``flat_vector`` MVP retrieval: scope = notebook ∩ allowed, embed the query,
        search, assemble. ``req.k`` is passed straight through to the SQL ``LIMIT`` — no
        over-fetch (no reranker exists in F31 to justify fetching more than asked for)."""
        notebook_docs = await knowledge_service.list_notebook_documents(ctx, req.notebook_id)
        allowed = set(await resolve_allowed_documents(ctx))
        scope = [doc.id for doc in notebook_docs if doc.id in allowed]
        if not scope:
            return assemble_context(req.query, [])

        [query_vector] = await embedder.embed([req.query])
        hits = await ingestion_service.search_chunks(
            ctx,
            query_vector=query_vector,
            document_ids=scope,
            model=embedder.model,
            k=req.k,
        )
        return assemble_context(req.query, hits)


retrieval_service = RetrievalService()
