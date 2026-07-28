"""Retrieval use cases — the MVP ``flat_vector`` path (architecture.md "Retrieval
pipeline"). Owns no table; reaches other modules only through their ``service``
(module-boundary rule): ``knowledge.service`` for notebook membership, ``documents.service``
for the allowed-documents hook, ``ingestion.service`` for the actual kNN search.
"""

from __future__ import annotations

import uuid

from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.ingestion import ChunkHit
from app.models.retrieval import ContextBlock, RetrievalSearchRequest, RetrievalSearchResponse
from app.services.access_roles import (
    resolve_access_controlling_tags,
    resolve_folder_effective_tags,
    resolve_user_granted_tags,
)
from app.services.documents import documents_service
from app.services.ingestion import ingestion_service
from app.services.knowledge import knowledge_service
from app.services.seams import Embedder, Reranker
from app.utils.constants import ADMIN_ROLES

logger = get_logger(__name__)


async def resolve_allowed_documents(ctx: TenantContext) -> list[uuid.UUID]:
    """The single seam where V2 groups/grants permission logic slots in (architecture.md).

    ``owner``/``admin`` always see every org document (unchanged from the original MVP
    stub). For everyone else: a tag becomes "access-controlling" the moment it's granted
    to any Access Role (docs/access-roles-dnd-plan.md) — a resource carrying none of the
    org's access-controlling tags stays open to everyone, exactly as before this feature
    existed. A resource carrying one DOES gate: visible only to a member holding a role
    granted at least one of those tags. Folder tags are inherited down the subtree
    (``_inherited_folder_tags``); a document's own direct tags (``document_tags``) add to
    whatever it inherits from its folder chain. No caching: this runs fresh on every
    call, so granting/revoking a tag takes effect on the very next request."""
    docs = await documents_service.list_documents(ctx)
    if ctx.role in ADMIN_ROLES:
        return [d.id for d in docs]

    access_controlling = await resolve_access_controlling_tags(ctx)
    if not access_controlling:
        # Nobody has ever granted any tag to any Access Role — nothing is gated yet, so
        # skip the folder/document-tag fetches entirely.
        return [d.id for d in docs]

    folders = await documents_service.list_folders(ctx)
    inherited_folder_tags = resolve_folder_effective_tags(folders)
    doc_tag_ids = await documents_service.list_document_tag_ids_by_documents(
        ctx, [d.id for d in docs]
    )
    user_granted = await resolve_user_granted_tags(ctx)

    allowed: list[uuid.UUID] = []
    for doc in docs:
        folder_tags = inherited_folder_tags.get(doc.folder_id, set()) if doc.folder_id else set()
        effective_tags = folder_tags | set(doc_tag_ids.get(doc.id, []))
        gating_tags = effective_tags & access_controlling
        if not gating_tags or (gating_tags & user_granted):
            allowed.append(doc.id)
    return allowed


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
            rerank_score=hit.rerank_score,
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
        reranker: Reranker,
    ) -> RetrievalSearchResponse:
        """``flat_vector`` MVP retrieval: scope = notebook ∩ allowed, embed the query,
        search, assemble. ``req.k`` is passed straight through to the SQL ``LIMIT`` when
        reranking is disabled (the default) — no over-fetch. When
        ``RERANKER_ENABLED``, ``_retrieve_hits`` widens the candidate pool and reranks it
        back down to ``req.k`` (see its docstring). When ``HIERARCHICAL_RETRIEVAL_ENABLED``,
        uses coarse-to-fine search with fallback to flat if enrichment is incomplete —
        orthogonal to reranking, which wraps whichever strategy's output it receives."""
        notebook_docs = await knowledge_service.list_notebook_documents(ctx, req.notebook_id)
        allowed = set(await resolve_allowed_documents(ctx))
        scope = [doc.id for doc in notebook_docs if doc.id in allowed]
        if not scope:
            return assemble_context(req.query, [])

        [query_vector] = await embedder.embed([req.query])
        hits = await self._retrieve_hits(
            ctx,
            query_vector,
            scope,
            embedder.model,
            req.k,
            reranker=reranker,
            query=req.query,
        )
        return assemble_context(req.query, hits)

    async def _retrieve_hits(
        self,
        ctx: TenantContext,
        query_vector: list[float],
        scope: list[uuid.UUID],
        model: str,
        k: int,
        *,
        reranker: Reranker,
        query: str,
    ) -> list[ChunkHit]:
        """Sources candidate hits (flat or hierarchical, per ``_search_hits``), then — when
        ``RERANKER_ENABLED`` — wraps the FINAL chunk-level output with one more
        transformation step: widen the chunk kNN pool to
        ``candidate_k = max(k, RERANK_CANDIDATE_K)`` (mirrors the existing
        ``s = max(k, HIERARCHICAL_TOP_SECTIONS)`` widening pattern), then
        ``reranker.rerank`` reorders + truncates it to ``final_k = min(k, RERANK_TOP_K)``.
        The reranker is agnostic to whether ``_search_hits`` used flat or hierarchical
        sourcing — it is one more step on top, never a competing strategy. Gate OFF
        (default): ``candidate_k == k``, ``reranker`` is never called, byte-identical to
        before this feature existed."""
        candidate_k = max(k, settings.RERANK_CANDIDATE_K) if settings.RERANKER_ENABLED else k
        hits = await self._search_hits(ctx, query_vector, scope, model, candidate_k, k, query=query)
        if settings.RERANKER_ENABLED:
            final_k = min(k, settings.RERANK_TOP_K)
            hits = await reranker.rerank(query, hits, final_k)
        return hits

    async def _search_hits(
        self,
        ctx: TenantContext,
        query_vector: list[float],
        scope: list[uuid.UUID],
        model: str,
        search_k: int,
        section_k: int,
        *,
        query: str,
    ) -> list[ChunkHit]:
        """Retrieve candidate hits using the active strategy: hierarchical (coarse-to-fine)
        if enabled, flat otherwise. Hierarchical always has a fallback to flat if the
        corpus lacks enrichment or returns no results. ``search_k`` sizes the chunk-level
        kNN ``LIMIT`` (the reranker-widened candidate pool when reranking is on, else
        plain ``k``); ``section_k`` sizes the coarse section-level pass — unwidened by the
        reranker, matching pre-reranker behavior exactly."""
        if not settings.HIERARCHICAL_RETRIEVAL_ENABLED:
            return await ingestion_service.search_chunks(
                ctx, query_vector=query_vector, document_ids=scope, model=model, k=search_k
            )

        # Coarse pass: search section embeddings.
        section_hits = await ingestion_service.search_sections(
            ctx,
            query_vector=query_vector,
            document_ids=scope,
            model=model,
            s=max(section_k, settings.HIERARCHICAL_TOP_SECTIONS),
        )
        if not section_hits:
            # No section embeddings exist (enrichment hasn't run yet) — fall back to flat.
            logger.info(
                "retrieval.hierarchical_fallback_no_sections",
                org_id=str(ctx.org_id),
                scope_count=len(scope),
            )
            return await ingestion_service.search_chunks(
                ctx, query_vector=query_vector, document_ids=scope, model=model, k=search_k
            )

        # Fine pass: search chunks within those sections.
        section_ids = [h.section_id for h in section_hits]
        hits = await ingestion_service.search_chunks(
            ctx,
            query_vector=query_vector,
            document_ids=scope,
            model=model,
            k=search_k,
            section_ids=section_ids,
        )
        if not hits:
            # Hierarchical filtered to zero results — fall back to flat.
            logger.info(
                "retrieval.hierarchical_fallback_no_chunks",
                org_id=str(ctx.org_id),
                section_count=len(section_hits),
            )
            return await ingestion_service.search_chunks(
                ctx, query_vector=query_vector, document_ids=scope, model=model, k=search_k
            )

        # Hierarchical succeeded.
        logger.info(
            "retrieval.hierarchical_used",
            org_id=str(ctx.org_id),
            section_count=len(section_hits),
            chunk_count=len(hits),
        )
        # V2 enrichment topics are otherwise dormant (never in the response shape by
        # design) — surface them at DEBUG for operator visibility of the coarse pass.
        logger.debug(
            "retrieval.section_topics",
            org_id=str(ctx.org_id),
            sections=[{"heading": h.heading, "topics": h.topics} for h in section_hits],
        )
        return hits


retrieval_service = RetrievalService()
