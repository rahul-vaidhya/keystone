"""``POST /retrieval/sparse-search`` orchestration: run one ranked / Boolean / phrase query
against the from-scratch sparse index (``sparse_channel.get_index_with_status``) and
return the query-processing trace alongside the hits, so every IR step is visible in the
Search page.

Scope (notebook ∩ allowed documents) is resolved by the caller,
``RetrievalService.sparse_search`` — the same ``resolve_notebook_scope`` the semantic
``/retrieval/search`` uses. Chunk page ranges come from ``ingestion_service.get_chunks``
(the chat citation page derivation) and document titles from ``documents_service`` —
module-boundary rule: services only, no SQL here.
"""

from __future__ import annotations

import time
import uuid

from app.middleware.context import TenantContext
from app.models.retrieval import (
    SparseBooleanClause,
    SparseBooleanOperand,
    SparseBooleanStep,
    SparseBooleanTrace,
    SparseContribution,
    SparseIndexStats,
    SparsePhraseMatch,
    SparsePhraseTerm,
    SparsePhraseTrace,
    SparsePhraseZone,
    SparseQueryAnalysis,
    SparseRankedTrace,
    SparseSearchRequest,
    SparseSearchResponse,
    SparseSearchResult,
    SparseTermStat,
)
from app.services.documents import documents_service
from app.services.ingestion import ingestion_service
from app.services.retrieval import sparse_channel
from app.services.retrieval.sparse import InvertedIndex
from app.services.retrieval.sparse.trace import (
    BOOLEAN_OPERATORS,
    boolean_search_trace,
    index_avg_postings_length,
    matching_surface_forms,
    phrase_occurrence_spans,
    phrase_search_trace,
    query_pipeline,
    ranked_search_trace,
    snippet_around,
    term_stats,
    validate_boolean_query,
)


def _analysis_text(req: SparseSearchRequest) -> str:
    """The text whose analysis pipeline is shown: Boolean operators and phrase quotes
    are syntax, not query words."""
    if req.mode == "boolean":
        return " ".join(w for w in req.query.split() if w not in BOOLEAN_OPERATORS)
    return _phrase_text(req.query) if req.mode == "phrase" else req.query


def _phrase_text(query: str) -> str:
    return query.strip().strip("\"“”'").strip()


def _ordered_by_position(doc_ids: list[str], chunks: dict) -> list[str]:
    return sorted(doc_ids, key=lambda d: (str(chunks[d].document_id), chunks[d].char_start))


async def run_sparse_search(
    ctx: TenantContext, req: SparseSearchRequest, scope: list[uuid.UUID]
) -> SparseSearchResponse:
    if req.mode == "boolean":
        validate_boolean_query(req.query)  # MalformedBooleanQuery -> 422, before any work
    started = time.perf_counter()
    if scope:
        cached, was_cached, build_ms = await sparse_channel.get_index_with_status(ctx, scope)
        index, chunks = cached.index, cached.chunks
    else:
        index, chunks, was_cached, build_ms = InvertedIndex.build([]), {}, False, 0.0

    query_started = time.perf_counter()
    pipeline = query_pipeline(_analysis_text(req))
    stats = term_stats(
        index, pipeline, idf_threshold=req.idf_threshold if req.mode == "ranked" else None
    )
    analysis = SparseQueryAnalysis(
        raw_tokens=pipeline.raw_tokens,
        casefolded=pipeline.casefolded,
        stop_words_removed=pipeline.stop_words_removed,
        kept_tokens=pipeline.kept_tokens,
        stems=pipeline.stems,
        terms=[SparseTermStat(**vars(s)) for s in stats],
    )

    ordered: list[str] = []
    scores: dict[str, float] = {}
    contributions: dict[str, list[SparseContribution]] = {}
    matched_terms: dict[str, list[str]] = {}
    phrase_matches: dict[str, list[SparsePhraseMatch]] = {}
    phrase_len = 0

    if req.mode == "ranked":
        zone_weights = {"heading": req.zone_weights.heading, "body": req.zone_weights.body}
        hits, rtrace = ranked_search_trace(
            index,
            req.query,
            k=req.k,
            scheme=req.scheme,
            zone_weights=zone_weights,
            use_champions=req.use_champions,
            idf_threshold=req.idf_threshold,
        )
        analysis.ranked = SparseRankedTrace(
            scheme=req.scheme,
            zone_weights=zone_weights,
            use_champions=req.use_champions,
            idf_threshold=req.idf_threshold,
            active_terms=rtrace.active_terms,
            eliminated_terms=rtrace.eliminated_terms,
            docs_with_postings=rtrace.docs_with_postings,
            champion_candidates=rtrace.champion_candidates,
            docs_scored=rtrace.docs_scored,
        )
        total = rtrace.docs_scored
        for hit in hits:
            ordered.append(hit.doc_id)
            scores[hit.doc_id] = hit.score
            contributions[hit.doc_id] = [SparseContribution(**vars(c)) for c in hit.contributions]
            matched_terms[hit.doc_id] = list(dict.fromkeys(c.term for c in hit.contributions))
    elif req.mode == "boolean":
        btrace = boolean_search_trace(index, req.query)
        analysis.boolean = SparseBooleanTrace(
            operators=btrace.operators,
            clauses=[
                SparseBooleanClause(
                    operands=[SparseBooleanOperand(**vars(o)) for o in c.operands],
                    steps=[SparseBooleanStep(**vars(s)) for s in c.steps],
                    result_size=c.result_size,
                )
                for c in btrace.clauses
            ],
            union_steps=[SparseBooleanStep(**vars(s)) for s in btrace.union_steps],
        )
        positive = list(
            dict.fromkeys(
                t for c in btrace.clauses for o in c.operands if not o.negated for t in o.terms
            )
        )
        total = len(btrace.result)
        ordered = _ordered_by_position(btrace.result, chunks)[: req.k]
        postings_sets = {t: set(index.doc_postings(t)) for t in positive}
        for doc_id in ordered:
            matched_terms[doc_id] = [t for t in positive if doc_id in postings_sets[t]]
    else:
        phrase = _phrase_text(req.query)
        ptrace = phrase_search_trace(index, phrase)
        analysis.phrase = SparsePhraseTrace(
            phrase=phrase,
            terms=[SparsePhraseTerm(term=t, offset=o) for t, o in ptrace.offsets],
            zones=[SparsePhraseZone(**vars(z)) for z in ptrace.zones],
            candidates=ptrace.candidates,
            matched=len(ptrace.result),
        )
        total = len(ptrace.result)
        ordered = _ordered_by_position(ptrace.result, chunks)[: req.k]
        phrase_stems = list(dict.fromkeys(t for t, _ in ptrace.offsets))
        phrase_len = ptrace.offsets[-1][1] + 1 if ptrace.offsets else 0
        for doc_id in ordered:
            matched_terms[doc_id] = phrase_stems
            phrase_matches[doc_id] = [
                SparsePhraseMatch(zone=z, positions=p) for z, p in ptrace.matches[doc_id]
            ]
    query_ms = (time.perf_counter() - query_started) * 1000.0

    chunk_ids = [chunks[d].chunk_id for d in ordered]
    doc_ids = list(dict.fromkeys(chunks[d].document_id for d in ordered))
    pages, titles = {}, {}
    if ordered:
        pages = {c.chunk_id: c for c in await ingestion_service.get_chunks(ctx, chunk_ids)}
        titles = {d.id: d.title for d in await documents_service.list_by_ids(ctx, doc_ids)}

    results: list[SparseSearchResult] = []
    for rank, doc_id in enumerate(ordered, start=1):
        chunk = chunks[doc_id]
        words = matching_surface_forms(chunk.content, matched_terms.get(doc_id, []))
        anchor = None
        if phrase_len:
            # Centre the snippet on the first VERIFIED occurrence (body-zone positions ->
            # char spans) and highlight the whole phrase, not its words wherever they are.
            body = [
                p for m in phrase_matches.get(doc_id, []) if m.zone == "body" for p in m.positions
            ]
            spans = phrase_occurrence_spans(chunk.content, body, phrase_len)
            if spans:
                anchor = spans[0]
                words = list(dict.fromkeys(chunk.content[a:b] for a, b in spans))
        record = pages.get(chunk.chunk_id)
        results.append(
            SparseSearchResult(
                rank=rank,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=titles.get(chunk.document_id),
                heading=chunk.heading,
                content=chunk.content,
                snippet=snippet_around(chunk.content, words, anchor=anchor),
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                page_start=record.page_start if record else None,
                page_end=record.page_end if record else None,
                score=scores.get(doc_id),
                contributions=contributions.get(doc_id, []),
                matched_terms=matched_terms.get(doc_id, []),
                highlights=words,
                phrase_matches=phrase_matches.get(doc_id, []),
            )
        )

    return SparseSearchResponse(
        query=req.query,
        mode=req.mode,
        analysis=analysis,
        index_stats=SparseIndexStats(
            n_docs=index.n_docs,
            vocabulary_size=index.vocabulary_size(),
            avg_postings_length=index_avg_postings_length(index),
            zones=index.zones,
            champion_r=index.champion_r,
            cached=was_cached,
            build_ms=build_ms,
            query_ms=query_ms,
            total_ms=(time.perf_counter() - started) * 1000.0,
        ),
        total_matches=total,
        results=results,
    )
