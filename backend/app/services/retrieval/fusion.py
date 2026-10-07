"""Pure retrieval-result transforms — no SQL, no seam calls, no side effects. Separate
from ``service.py`` (orchestration) and ``permissions.py`` (a different data flow
entirely: documents/folders/tags, never chunks/embeddings).
"""

from __future__ import annotations

import uuid

from app.models.ingestion import ChunkHit
from app.models.retrieval import ContextBlock, RetrievalSearchResponse


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
            sparse_score=hit.sparse_score,
            sparse_explanation=hit.sparse_explanation,
            fused_score=hit.fused_score,
            vector_rank=hit.vector_rank,
            lexical_rank=hit.lexical_rank,
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
        # The in-house sparse channel's lexical instance carries a score + explanation;
        # keep them on whichever instance survived dedup (in either input order). Never
        # fires on the Postgres lexical path, where sparse_score is always None.
        source = current if current is not None and current.sparse_score is not None else hit
        kept = hit_by_id[hit.chunk_id]
        if source.sparse_score is not None and kept.sparse_score is None:
            hit_by_id[hit.chunk_id] = kept.model_copy(
                update={
                    "sparse_score": source.sparse_score,
                    "sparse_explanation": source.sparse_explanation,
                }
            )

    vector_rank = {hit.chunk_id: rank for rank, hit in enumerate(vector_hits, start=1)}
    lexical_rank = {hit.chunk_id: rank for rank, hit in enumerate(lexical_hits, start=1)}
    fused = [
        hit.model_copy(
            update={
                "fused_score": scores[hit.chunk_id],
                "vector_rank": vector_rank.get(hit.chunk_id),
                "lexical_rank": lexical_rank.get(hit.chunk_id),
            }
        )
        for hit in hit_by_id.values()
    ]
    return sorted(fused, key=lambda hit: hit.fused_score, reverse=True)
