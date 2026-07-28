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


def fuse_rrf(
    vector_hits: list[ChunkHit], lexical_hits: list[ChunkHit], *, k: int = 60
) -> list[ChunkHit]:
    """Reciprocal Rank Fusion — combines a vector-kNN ranked list and a lexical
    (native-Postgres-full-text) ranked list into one ranked, deduplicated list (hybrid
    search, gated by ``HYBRID_SEARCH_ENABLED``). Standard RRF: each hit's fused score is
    ``sum(1 / (k + rank))`` over every ranked list it appears in, where ``rank`` is its
    1-indexed position within that list — a chunk present in both lists sums both
    contributions, so a chunk ranked highly in BOTH lists outranks one ranked highly in
    only one. Dedup key is ``chunk_id``: when a chunk appears in both lists, the
    VECTOR-hit ``ChunkHit`` instance is kept (it carries a real ``distance``; the
    lexical-only instance's ``distance`` is always ``None``), carrying forward whichever
    instance's ``rerank_score`` is set (in practice neither will be — fusion always runs
    BEFORE reranking in the pipeline). Pure function: no SQL, no side effects, no
    truncation — the caller (``_search_hits``) decides the final candidate/result size,
    same as it always has."""
    scores: dict[uuid.UUID, float] = {}
    for ranked_list in (vector_hits, lexical_hits):
        for rank, hit in enumerate(ranked_list, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)

    hit_by_id: dict[uuid.UUID, ChunkHit] = {}
    for hit in [*vector_hits, *lexical_hits]:
        current = hit_by_id.get(hit.chunk_id)
        if current is None:
            hit_by_id[hit.chunk_id] = hit
        elif current.distance is None and hit.distance is not None:
            # A lexical-only instance was recorded first (fusion called with lexical
            # hits before vector hits) — replace it with the vector instance, carrying
            # forward whichever instance already had a rerank_score.
            rerank_score = (
                current.rerank_score if current.rerank_score is not None else hit.rerank_score
            )
            hit_by_id[hit.chunk_id] = hit.model_copy(update={"rerank_score": rerank_score})

    return sorted(hit_by_id.values(), key=lambda hit: scores[hit.chunk_id], reverse=True)


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
        reranker, matching pre-reranker behavior exactly. Every chunk-level candidate
        call goes through ``_vector_and_maybe_hybrid_search`` (below), which ALSO widens
        and fuses in a lexical candidate list when ``HYBRID_SEARCH_ENABLED`` — orthogonal
        to which strategy (flat/hierarchical) sourced ``search_k``, and orthogonal to the
        reranker, exactly like the reranker is orthogonal to hierarchical."""
        if not settings.HIERARCHICAL_RETRIEVAL_ENABLED:
            return await self._vector_and_maybe_hybrid_search(
                ctx, query, query_vector, scope, model, search_k
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
            return await self._vector_and_maybe_hybrid_search(
                ctx, query, query_vector, scope, model, search_k
            )

        # Fine pass: search chunks within those sections.
        section_ids = [h.section_id for h in section_hits]
        hits = await self._vector_and_maybe_hybrid_search(
            ctx, query, query_vector, scope, model, search_k, section_ids=section_ids
        )
        if not hits:
            # Hierarchical filtered to zero results — fall back to flat.
            logger.info(
                "retrieval.hierarchical_fallback_no_chunks",
                org_id=str(ctx.org_id),
                section_count=len(section_hits),
            )
            return await self._vector_and_maybe_hybrid_search(
                ctx, query, query_vector, scope, model, search_k
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

    async def _vector_and_maybe_hybrid_search(
        self,
        ctx: TenantContext,
        query: str,
        query_vector: list[float],
        scope: list[uuid.UUID],
        model: str,
        k: int,
        *,
        section_ids: list[uuid.UUID] | None = None,
    ) -> list[ChunkHit]:
        """Sources the vector-kNN candidate list (flat or hierarchical fine-pass, per the
        caller), and — only when ``HYBRID_SEARCH_ENABLED`` — ALSO sources a lexical
        (native Postgres full-text) candidate list over the same scope/``section_ids``,
        then fuses both via ``fuse_rrf``. Widens EACH list independently to
        ``max(k, HYBRID_CANDIDATE_K)`` before fusion (mirrors the reranker's
        ``max(k, RERANK_CANDIDATE_K)`` widening pattern) — applied on top of whatever
        ``k`` the caller already passed (which may itself already be
        reranker-widened), so when both reranking AND hybrid are on the effective
        candidate pool is ``max(k, RERANK_CANDIDATE_K, HYBRID_CANDIDATE_K)``. ``fuse_rrf``
        itself never truncates; this method truncates the FUSED list back to the ``k`` it
        received (not the widened pool) so the "returns at most k" contract every prior
        ``ingestion_service.search_chunks(..., k=...)`` call site already guaranteed keeps
        holding — when reranking is also on, that ``k`` is itself the reranker-widened
        ``candidate_k``, so the reranker still receives exactly ``candidate_k`` candidates
        to rerank+truncate to ``final_k``, unchanged. Gate OFF (default): a single vector
        call at exactly ``k``, byte-identical to every
        ``ingestion_service.search_chunks(...)`` call site this replaces."""
        vector_k = max(k, settings.HYBRID_CANDIDATE_K) if settings.HYBRID_SEARCH_ENABLED else k
        vector_hits = await ingestion_service.search_chunks(
            ctx,
            query_vector=query_vector,
            document_ids=scope,
            model=model,
            k=vector_k,
            section_ids=section_ids,
        )
        if not settings.HYBRID_SEARCH_ENABLED:
            return vector_hits

        lexical_k = max(k, settings.HYBRID_CANDIDATE_K)
        lexical_hits = await ingestion_service.search_chunks_lexical(
            ctx, query=query, document_ids=scope, k=lexical_k, section_ids=section_ids
        )
        # Deliberately no logger call on this path: `app.services.retrieval`'s module
        # logger is SHARED with hierarchical retrieval's own logging, and
        # `cache_logger_on_first_use=True` (config/logging.py) permanently locks that
        # ONE shared proxy's level in at whatever it was on its first-ever call —
        # adding a call here that fires from a HYBRID test in test_retrieval.py (which
        # sorts alphabetically before test_retrieval_hierarchical.py) would lock the
        # logger at INFO before that file's own DEBUG-level log-capture test runs,
        # breaking it. See memory.md's structlog `capture_logs()` gotcha.
        return fuse_rrf(vector_hits, lexical_hits)[:k]


retrieval_service = RetrievalService()
