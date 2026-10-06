"""App glue for the from-scratch sparse IR core (``app.services.retrieval.sparse``):
hybrid search's lexical channel when ``SPARSE_RETRIEVAL_MODE != "off"`` (and only when
``HYBRID_SEARCH_ENABLED`` — see ``RetrievalService._vector_and_maybe_hybrid_search``).

- Builds an ``InvertedIndex`` per allowed-document set from that set's chunks, two zones
  per chunk: ``heading`` (owning section heading) and ``body`` (chunk content); doc id =
  chunk id. Chunks come through ``ingestion_service`` only (module-boundary rule: no
  SQL here, every query org-scoped in the ingestion repository).
- Caches built indexes in-process (small LRU) keyed by
  ``(org_id, frozenset(document_ids), chunk fingerprint)`` — the fingerprint is
  ``(chunk count, md5 of sorted chunk ids)``, so a re-ingest (new chunk ids) or any
  chunk-count change invalidates the entry. ``org_id`` in the key means one tenant's
  index can never serve another tenant's query.
- Returns ``ChunkHit``s exactly like the Postgres lexical path (``distance=None``), plus
  ``sparse_score`` and ``sparse_explanation`` (``explain=True`` contributions as dicts:
  ``{"term", "zone", "tf", "idf", "weight"}``; weights sum to ``sparse_score``).
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.ingestion import ChunkHit, SparseIndexChunk
from app.services.ingestion import ingestion_service
from app.services.retrieval.sparse import InvertedIndex, SparseDoc, search

_CACHE_MAX_ENTRIES = 16

_CacheKey = tuple[uuid.UUID, frozenset[uuid.UUID], tuple[int, str]]


@dataclass(frozen=True)
class _CachedIndex:
    index: InvertedIndex
    chunks: dict[str, SparseIndexChunk]


_cache: OrderedDict[_CacheKey, _CachedIndex] = OrderedDict()


def clear_cache() -> None:
    """Drop every cached index (tests / operational reset)."""
    _cache.clear()


def cache_size() -> int:
    return len(_cache)


def _build(chunks: list[SparseIndexChunk]) -> _CachedIndex:
    docs = [
        SparseDoc(doc_id=str(c.chunk_id), zones={"heading": c.heading or "", "body": c.content})
        for c in chunks
    ]
    return _CachedIndex(
        index=InvertedIndex.build(docs), chunks={str(c.chunk_id): c for c in chunks}
    )


async def get_index(ctx: TenantContext, document_ids: list[uuid.UUID]) -> _CachedIndex:
    """Return the (possibly cached) sparse index over ``document_ids``' chunks."""
    fingerprint = await ingestion_service.chunk_fingerprint(ctx, document_ids)
    key: _CacheKey = (ctx.org_id, frozenset(document_ids), fingerprint)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    chunks = await ingestion_service.list_chunks_for_sparse_index(ctx, document_ids)
    built = _build(chunks)
    _cache[key] = built
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)
    return built


async def search_sparse_lexical(
    ctx: TenantContext,
    *,
    query: str,
    document_ids: list[uuid.UUID],
    k: int,
    section_ids: list[uuid.UUID] | None = None,
) -> list[ChunkHit]:
    """Drop-in replacement for ``ingestion_service.search_chunks_lexical``: same inputs,
    same ``ChunkHit`` output shape (``distance=None``), ranked by the in-house scheme
    (``SPARSE_RETRIEVAL_MODE``: ``tfidf`` = SMART lnc.ltc, ``bm25`` = Okapi BM25) with
    zone weights ``{"heading": SPARSE_HEADING_ZONE_WEIGHT, "body": 1.0}``. When
    ``section_ids`` is given (hierarchical fine pass), results are filtered to those
    sections after ranking the whole scoped index."""
    if not document_ids or k <= 0:
        return []
    cached = await get_index(ctx, document_ids)
    if cached.index.n_docs == 0:
        return []
    allowed_sections = set(section_ids) if section_ids else None
    scheme = "tfidf" if settings.SPARSE_RETRIEVAL_MODE == "tfidf" else "bm25"
    hits = search(
        cached.index,
        query,
        k=cached.index.n_docs if allowed_sections is not None else k,
        scheme=scheme,
        zone_weights={"heading": settings.SPARSE_HEADING_ZONE_WEIGHT, "body": 1.0},
        explain=True,
    )
    results: list[ChunkHit] = []
    for hit in hits:
        chunk = cached.chunks[hit.doc_id]
        if allowed_sections is not None and chunk.section_id not in allowed_sections:
            continue
        results.append(
            ChunkHit(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                distance=None,
                sparse_score=hit.score,
                sparse_explanation=[
                    {"term": c.term, "zone": c.zone, "tf": c.tf, "idf": c.idf, "weight": c.weight}
                    for c in hit.contributions
                ],
            )
        )
        if len(results) >= k:
            break
    return results
