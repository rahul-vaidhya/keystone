"""Retrieval use cases — the MVP ``flat_vector`` path (architecture.md "Retrieval
pipeline"). Owns no table; reaches other modules only through their ``service``
(module-boundary rule): ``knowledge.service`` for notebook membership,
``permissions.resolve_allowed_documents`` for the allowed-documents hook,
``ingestion.service`` for the actual kNN search.
"""

from __future__ import annotations

import uuid

from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.ingestion import ChunkHit
from app.models.retrieval import RetrievalSearchRequest, RetrievalSearchResponse
from app.services.ingestion import ingestion_service
from app.services.knowledge import knowledge_service
from app.services.retrieval import sparse_channel
from app.services.retrieval.fusion import assemble_context, fuse_rrf
from app.services.retrieval.permissions import resolve_allowed_documents
from app.services.seams import Embedder, Reranker, SeamTransientError

logger = get_logger(__name__)
# A separate logger, not the shared module `logger` above: structlog's
# cache_logger_on_first_use=True bakes in whatever level was active the first time a
# given logger name is actually used, and `logger` is shared with hierarchical
# retrieval's tests, which rely on a temporary DEBUG wrapper_class swap around their
# OWN first use of it — an INFO call through the shared logger from anywhere else,
# running first, would permanently cache it at INFO and silently break that DEBUG
# capture (this is the exact gap memory.md's hybrid-search feature deliberately left
# unaddressed rather than risk). A distinct name sidesteps the shared cache entirely.
_reranker_logger = get_logger(f"{__name__}.reranker_fallback")


class RetrievalService:
    async def resolve_notebook_scope(
        self, ctx: TenantContext, notebook_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Notebook ∩ allowed-documents scope resolution — the exact computation
        ``search`` needs before choosing a retrieval strategy, extracted so the P1
        broad-query map-reduce path (``app.services.chat.broad_query``, a 4th retrieval
        strategy alongside flat/hierarchical/hybrid) can resolve the SAME scoped
        document-id list without duplicating the ``knowledge_service``/``permissions``
        wiring. Zero behavior change to ``search`` itself — this is the same two lines
        it always ran, just named and reusable."""
        notebook_docs = await knowledge_service.list_notebook_documents(ctx, notebook_id)
        allowed = set(await resolve_allowed_documents(ctx))
        return [doc.id for doc in notebook_docs if doc.id in allowed]

    async def get_sparse_index(self, ctx: TenantContext, document_ids: list[uuid.UUID]):
        """The (cached) from-scratch sparse index over ``document_ids``' chunks — the
        public entry point other domains (the chat citation checker) use to reach
        ``sparse_channel.get_index`` (module-boundary rule: through this service only).
        Works regardless of ``SPARSE_RETRIEVAL_MODE``."""
        return await sparse_channel.get_index(ctx, document_ids)

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
        scope = await self.resolve_notebook_scope(ctx, req.notebook_id)
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
        before this feature existed.

        A transient reranker failure (timeout, connection error, 5xx — anything
        ``RealReranker`` classifies and wraps as ``SeamTransientError``) degrades to the
        unreranked candidates truncated to ``final_k`` rather than failing the whole chat
        turn — same fallback shape as ``_search_hits``'s hierarchical-to-flat degradation.
        Non-transient exceptions (a real bug, a misconfigured URL) still propagate."""
        candidate_k = max(k, settings.RERANK_CANDIDATE_K) if settings.RERANKER_ENABLED else k
        hits = await self._search_hits(ctx, query_vector, scope, model, candidate_k, k, query=query)
        if settings.RERANKER_ENABLED:
            final_k = min(k, settings.RERANK_TOP_K)
            try:
                hits = await reranker.rerank(query, hits, final_k)
            except SeamTransientError:
                _reranker_logger.info("retrieval.reranker_fallback", candidate_count=len(hits))
                hits = hits[:final_k]
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
        if settings.SPARSE_RETRIEVAL_MODE != "off":
            # From-scratch sparse IR core (tf-idf / BM25 over an in-house positional zone
            # index) replaces ONLY the lexical channel; fusion/rerank/gate unchanged.
            lexical_hits = await sparse_channel.search_sparse_lexical(
                ctx, query=query, document_ids=scope, k=lexical_k, section_ids=section_ids
            )
        else:
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
