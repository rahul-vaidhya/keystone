"""Integration tests for the from-scratch sparse IR lexical channel
(``SPARSE_RETRIEVAL_MODE``, ``app.services.retrieval.sparse_channel``): gate-off
regression, flag-on lexical match with a populated explanation, heading-zone matching,
cross-org isolation, and cache invalidation."""

from __future__ import annotations

import uuid

import pytest

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding, Section
from app.services.ingestion import ingestion_service
from app.services.retrieval import assemble_context, retrieval_service, sparse_channel
from app.services.seams import EMBED_DIM

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


class _NoReranker:
    async def rerank(self, query, candidates, top_k):  # pragma: no cover - never called
        raise AssertionError("reranker must not be called (RERANKER_ENABLED=False)")


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", False)
    sparse_channel.clear_cache()
    yield
    sparse_channel.clear_cache()


def _vector(seed: int) -> list[float]:
    vec = [0.0] * EMBED_DIM
    vec[seed % EMBED_DIM] = 1.0
    return vec


async def _seed_org_and_document(session_factory, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    org_id, doc_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name=f"sparse-{name}"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title=f"sparse-{name}"))
    return org_id, doc_id


async def _seed_section(session_factory, org_id, doc_id, heading: str) -> uuid.UUID:
    section_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Section(
                id=section_id,
                org_id=org_id,
                document_id=doc_id,
                parent_section_id=None,
                ordinal=0,
                depth=1,
                path="1",
                heading=heading,
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=100,
            )
        )
    return section_id


async def _seed_chunk(
    session_factory,
    org_id,
    doc_id,
    *,
    ordinal: int,
    content: str,
    seed: int,
    section_id: uuid.UUID | None = None,
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=doc_id,
                section_id=section_id,
                ordinal=ordinal,
                content=content,
                token_count=max(len(content) // 4, 1),
                char_start=0,
                char_end=len(content),
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=doc_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model=FAKE_MODEL,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return chunk_id


async def _seed_rare_term_corpus(session_factory, name: str):
    """A target chunk containing a rare term, embedded ORTHOGONAL to the query vector,
    plus decoys identical to the query vector — pure vector kNN at k=1 misses it."""
    org_id, doc_id = await _seed_org_and_document(session_factory, name)
    target = await _seed_chunk(
        session_factory,
        org_id,
        doc_id,
        ordinal=0,
        content="The device shipped with a rare component called Quarvex-7 attached.",
        seed=1,
    )
    for i in range(3):
        await _seed_chunk(
            session_factory,
            org_id,
            doc_id,
            ordinal=i + 1,
            content=f"generic filler paragraph number {i}",
            seed=0,
        )
    return org_id, doc_id, target


async def _retrieve(ctx, doc_id, query: str, k: int = 1):
    return await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=k,
        reranker=_NoReranker(),
        query=query,
    )


async def test_sparse_mode_off_is_byte_identical_postgres_lexical_path(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPARSE_RETRIEVAL_MODE=off (default): the in-house channel is never touched, the
    Postgres lexical path is used, and no sparse fields are populated."""
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "HYBRID_CANDIDATE_K", 5)
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", "off")

    async def _boom(*args, **kwargs):
        raise AssertionError("sparse channel must not run when SPARSE_RETRIEVAL_MODE=off")

    monkeypatch.setattr(sparse_channel, "search_sparse_lexical", _boom)
    org_id, doc_id, target = await _seed_rare_term_corpus(session_factory, "off")
    hits = await _retrieve(TenantContext(org_id=org_id), doc_id, "Quarvex-7")
    assert target in [h.chunk_id for h in hits]  # found by the Postgres lexical channel
    assert all(h.sparse_score is None and h.sparse_explanation is None for h in hits)
    assert sparse_channel.cache_size() == 0


async def test_sparse_mode_without_hybrid_never_runs_any_lexical_channel(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPARSE_RETRIEVAL_MODE only replaces hybrid's lexical channel — with hybrid off it
    is inert (vector-only, unchanged)."""
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", "bm25")

    async def _boom(*args, **kwargs):
        raise AssertionError("no lexical channel may run when HYBRID_SEARCH_ENABLED=False")

    monkeypatch.setattr(sparse_channel, "search_sparse_lexical", _boom)
    monkeypatch.setattr(ingestion_service, "search_chunks_lexical", _boom)
    org_id, doc_id, target = await _seed_rare_term_corpus(session_factory, "nohybrid")
    hits = await _retrieve(TenantContext(org_id=org_id), doc_id, "Quarvex-7")
    assert target not in [h.chunk_id for h in hits]
    assert all(h.sparse_score is None for h in hits)


@pytest.mark.parametrize("mode", ["bm25", "tfidf"])
async def test_sparse_mode_on_finds_lexical_match_with_explanation(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "HYBRID_CANDIDATE_K", 5)
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", mode)

    async def _boom(*args, **kwargs):
        raise AssertionError("Postgres lexical path must not run when sparse mode is on")

    monkeypatch.setattr(ingestion_service, "search_chunks_lexical", _boom)
    org_id, doc_id, target = await _seed_rare_term_corpus(session_factory, f"on-{mode}")
    ctx = TenantContext(org_id=org_id)

    hits = await _retrieve(ctx, doc_id, "Quarvex-7")
    target_hit = next(h for h in hits if h.chunk_id == target)
    # The widened vector pool (5 >= 4 chunks) also holds the target, so RRF keeps the
    # vector instance (real distance) — and must carry the sparse fields onto it.
    assert target_hit.distance is not None
    assert target_hit.sparse_score is not None and target_hit.sparse_score > 0
    explanation = target_hit.sparse_explanation
    assert explanation and {e["term"] for e in explanation} == {"quarvex", "7"}
    assert set(explanation[0]) == {"term", "zone", "tf", "idf", "weight"}
    assert all(e["zone"] == "body" and e["tf"] == 1 for e in explanation)
    assert sum(e["weight"] for e in explanation) == pytest.approx(target_hit.sparse_score)

    # Flows into ContextBlock and its JSON dump (what message_traces.hits persists).
    block = assemble_context("Quarvex-7", hits).results[0]
    dumped = block.model_dump(mode="json")
    assert dumped["sparse_explanation"] == explanation
    assert dumped["sparse_score"] == pytest.approx(target_hit.sparse_score)


async def test_sparse_heading_zone_matches_section_heading(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", "bm25")
    monkeypatch.setattr(settings, "SPARSE_HEADING_ZONE_WEIGHT", 2.0)
    org_id, doc_id = await _seed_org_and_document(session_factory, "heading")
    section_id = await _seed_section(session_factory, org_id, doc_id, "Electrovalent Bonding")
    under_heading = await _seed_chunk(
        session_factory,
        org_id,
        doc_id,
        ordinal=0,
        content="atoms transfer charge",
        seed=0,
        section_id=section_id,
    )
    await _seed_chunk(session_factory, org_id, doc_id, ordinal=1, content="unrelated", seed=1)
    hits = await sparse_channel.search_sparse_lexical(
        TenantContext(org_id=org_id), query="electrovalent", document_ids=[doc_id], k=5
    )
    assert [h.chunk_id for h in hits] == [under_heading]
    assert hits[0].sparse_explanation[0]["zone"] == "heading"

    # section_ids narrowing (hierarchical fine pass) keeps only those sections' chunks.
    narrowed = await sparse_channel.search_sparse_lexical(
        TenantContext(org_id=org_id),
        query="electrovalent",
        document_ids=[doc_id],
        k=5,
        section_ids=[uuid.uuid4()],
    )
    assert narrowed == []


async def test_sparse_channel_cross_org_isolation(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", "bm25")
    org_a, doc_a, target = await _seed_rare_term_corpus(session_factory, "iso-a")
    org_b, _ = await _seed_org_and_document(session_factory, "iso-b")

    own = await sparse_channel.search_sparse_lexical(
        TenantContext(org_id=org_a), query="Quarvex-7", document_ids=[doc_a], k=5
    )
    assert [h.chunk_id for h in own] == [target]
    # Org B naming org A's document id gets nothing: the repository filters org_id
    # independently, and org_id is part of the cache key so A's index is never reused.
    foreign = await sparse_channel.search_sparse_lexical(
        TenantContext(org_id=org_b), query="Quarvex-7", document_ids=[doc_a], k=5
    )
    assert foreign == []


async def test_sparse_channel_cache_reuse_and_invalidation(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SPARSE_RETRIEVAL_MODE", "bm25")
    org_id, doc_id, _ = await _seed_rare_term_corpus(session_factory, "cache")
    ctx = TenantContext(org_id=org_id)

    builds = 0
    real_list = ingestion_service.list_chunks_for_sparse_index

    async def _counting(*args, **kwargs):
        nonlocal builds
        builds += 1
        return await real_list(*args, **kwargs)

    monkeypatch.setattr(ingestion_service, "list_chunks_for_sparse_index", _counting)

    await sparse_channel.search_sparse_lexical(ctx, query="Zentrafel", document_ids=[doc_id], k=5)
    first = await sparse_channel.search_sparse_lexical(
        ctx, query="Zentrafel", document_ids=[doc_id], k=5
    )
    assert builds == 1 and first == []  # second call served from cache

    new_chunk = await _seed_chunk(
        session_factory, org_id, doc_id, ordinal=9, content="the Zentrafel valve", seed=2
    )
    after = await sparse_channel.search_sparse_lexical(
        ctx, query="Zentrafel", document_ids=[doc_id], k=5
    )
    assert builds == 2  # chunk fingerprint changed -> index rebuilt
    assert [h.chunk_id for h in after] == [new_chunk]
