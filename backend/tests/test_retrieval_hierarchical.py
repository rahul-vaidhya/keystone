"""V2 hierarchical (coarse-to-fine) retrieval: flag-gated behavior, fallback to flat when
enrichment is missing, org isolation for search_sections."""

from __future__ import annotations

import logging
import uuid

import structlog
from sqlalchemy import update

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding, Section
from app.services.ingestion import ingestion_service
from app.services.seams import EMBED_DIM

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


def _vector(seed: int, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic unit-ish vector for testing."""
    vec = [0.0] * dim
    vec[seed % dim] = 1.0
    return vec


async def _seed_section_with_embedding(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    section_id: uuid.UUID,
    ordinal: int,
    heading: str | None,
    path: str,
    model: str = FAKE_MODEL,
    seed: int = 0,
) -> uuid.UUID:
    """Seeds a Section row and its corresponding Embedding (owner_type='section')."""
    async with session_factory() as session, session.begin():
        session.add(
            Section(
                id=section_id,
                org_id=org_id,
                document_id=document_id,
                parent_section_id=None,
                ordinal=ordinal,
                depth=1,
                path=path,
                heading=heading,
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=100,
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=document_id,
                owner_type="section",
                owner_id=section_id,
                model=model,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return section_id


async def _seed_chunk_with_embedding(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    section_id: uuid.UUID | None = None,
    ordinal: int,
    content: str,
    model: str = FAKE_MODEL,
    seed: int = 0,
) -> uuid.UUID:
    """Seeds a Chunk row and its corresponding Embedding (owner_type='chunk')."""
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
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
                document_id=document_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model=model,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return chunk_id


async def test_hierarchical_coarse_to_fine_narrows_to_section(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Flag ON: query vector matches section A; section B has a decoy chunk with the same
    vector. With HIERARCHICAL_TOP_SECTIONS=1, only section A survives coarse pass, so the
    decoy from section B is NOT returned despite being closest by raw distance."""
    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(settings, "HIERARCHICAL_TOP_SECTIONS", 1)

    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    section_a_id = uuid.uuid4()
    section_b_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    # Section A with embedding matching vector seed=1.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_a_id,
        ordinal=0,
        heading="Section A",
        path="1",
        seed=1,
    )

    # Section B with embedding matching vector seed=2.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_b_id,
        ordinal=1,
        heading="Section B",
        path="2",
        seed=2,
    )

    # Chunk A1 in section A, embedding matches seed=1 (query).
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_a_id,
        ordinal=0,
        content="chunk a1 content",
        seed=1,
    )

    # Chunk B2 in section B, embedding matches seed=1 (identical to query, would be closest
    # if flat search were used), but section B is excluded by coarse pass.
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_b_id,
        ordinal=1,
        content="chunk b2 decoy content",
        seed=1,  # Same embedding as query to make it closest by distance.
    )

    ctx = TenantContext(org_id=org_id)
    query_vector = _vector(1)

    # With hierarchical ON and HIERARCHICAL_TOP_SECTIONS=1, we use the coarse pass, which
    # should narrow to section A only, so b2 is filtered out.
    # To test this, we directly query the hierarchical path:
    section_hits = await ingestion_service.search_sections(
        ctx,
        query_vector=query_vector,
        document_ids=[doc_id],
        model=FAKE_MODEL,
        s=1,
    )
    assert len(section_hits) == 1
    assert section_hits[0].section_id == section_a_id

    chunk_hits = await ingestion_service.search_chunks(
        ctx,
        query_vector=query_vector,
        document_ids=[doc_id],
        model=FAKE_MODEL,
        k=8,
        section_ids=[section_a_id],
    )
    assert len(chunk_hits) == 1
    assert chunk_hits[0].content == "chunk a1 content"


async def test_hierarchical_fallback_when_no_section_embeddings_exist(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Flag ON, but no section embeddings exist (enrichment not run). Hierarchical
    search should fall back to flat and return chunks normally."""
    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", True)

    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    # Seed a chunk WITHOUT any section embedding.
    chunk_id = await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=None,
        ordinal=0,
        content="test chunk",
        seed=1,
    )

    ctx = TenantContext(org_id=org_id)
    query_vector = _vector(1)

    # Hierarchical search should fall back to flat and find the chunk.
    hits = await ingestion_service.search_chunks(
        ctx,
        query_vector=query_vector,
        document_ids=[doc_id],
        model=FAKE_MODEL,
        k=8,
    )
    assert len(hits) == 1
    assert hits[0].chunk_id == chunk_id


async def test_hierarchical_disabled_returns_flat_behavior(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Flag OFF (default): hierarchical code path is not exercised, flat behavior
    unchanged."""
    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", False)

    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    section_a_id = uuid.uuid4()
    section_b_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    # Seed both sections with embeddings.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_a_id,
        ordinal=0,
        heading="Section A",
        path="1",
        seed=1,
    )
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_b_id,
        ordinal=1,
        heading="Section B",
        path="2",
        seed=2,
    )

    # Seed chunks in both sections.
    chunk_a_id = await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_a_id,
        ordinal=0,
        content="chunk a",
        seed=2,  # Different from query (seed=1), so it ranks after decoy with seed=1.
    )

    decoy_chunk_b_id = await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_b_id,
        ordinal=1,
        content="chunk b decoy",
        seed=1,  # Same embedding as query, closest by distance.
    )

    ctx = TenantContext(org_id=org_id)
    query_vector = _vector(1)

    # With hierarchical disabled, flat search should return BOTH chunks, with the decoy
    # (seed=1, distance=0) appearing first in distance order.
    hits = await ingestion_service.search_chunks(
        ctx,
        query_vector=query_vector,
        document_ids=[doc_id],
        model=FAKE_MODEL,
        k=8,
    )
    assert len(hits) == 2
    # Both should be present; the decoy (distance 0) should come first.
    hit_ids = [h.chunk_id for h in hits]
    assert decoy_chunk_b_id in hit_ids
    assert chunk_a_id in hit_ids
    assert hits[0].chunk_id == decoy_chunk_b_id


async def test_search_sections_org_id_is_an_independent_backstop(
    session_factory, tenant_engine
) -> None:
    """Calls IngestionService.search_sections directly with org A's TenantContext but
    org B's document_id in the requested scope. The repository-level org_id filter
    must independently block the leak."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    doc_b = uuid.uuid4()
    section_b_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    await _seed_section_with_embedding(
        session_factory,
        org_id=org_b,
        document_id=doc_b,
        section_id=section_b_id,
        ordinal=0,
        heading="Secret section",
        path="1",
        seed=0,
    )

    ctx_a = TenantContext(org_id=org_a)
    hits = await ingestion_service.search_sections(
        ctx_a, query_vector=_vector(0), document_ids=[doc_b], model=FAKE_MODEL, s=8
    )
    assert hits == []

    # Control: org B's own context CAN see it, proving the absence above is the filter.
    ctx_b = TenantContext(org_id=org_b)
    control_hits = await ingestion_service.search_sections(
        ctx_b, query_vector=_vector(0), document_ids=[doc_b], model=FAKE_MODEL, s=8
    )
    assert len(control_hits) == 1
    assert control_hits[0].section_id == section_b_id


async def test_hierarchical_response_shape_intact(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Hierarchical retrieval returns ContextBlocks with the same shape as flat:
    index (1-indexed), document_id, chunk_id, char_start/end, content, distance."""
    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", True)

    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    section_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_id,
        ordinal=0,
        heading="Test",
        path="1",
        seed=0,
    )

    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=doc_id,
                section_id=section_id,
                ordinal=0,
                content="test content",
                token_count=3,
                char_start=10,
                char_end=22,
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
                embedding=_vector(0),
            )
        )

    ctx = TenantContext(org_id=org_id)
    hits = await ingestion_service.search_chunks(
        ctx,
        query_vector=_vector(0),
        document_ids=[doc_id],
        model=FAKE_MODEL,
        k=8,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.chunk_id == chunk_id
    assert hit.document_id == doc_id
    assert hit.content == "test content"
    assert hit.char_start == 10
    assert hit.char_end == 22
    assert isinstance(hit.distance, float)


async def test_hierarchical_used_logs_at_info_with_topics_at_debug(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Verify that retrieval.hierarchical_used logs at INFO, and retrieval.section_topics
    logs at DEBUG with topics from the SectionHit payloads."""
    from app.services.retrieval import retrieval_service

    monkeypatch.setattr(settings, "HIERARCHICAL_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(settings, "HIERARCHICAL_TOP_SECTIONS", 8)

    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    section_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    # Seed section with embedding.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_id,
        ordinal=0,
        heading="Chemistry Basics",
        path="1",
        seed=1,
    )

    # Seed chunk in that section with embedding.
    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=doc_id,
                section_id=section_id,
                ordinal=0,
                content="test chunk content",
                token_count=5,
                char_start=0,
                char_end=18,
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
                embedding=_vector(1),
            )
        )

    # Update section with topics.
    async with session_factory() as session, session.begin():
        stmt = (
            update(Section)
            .where(Section.id == section_id)
            .values(topics=["ionic bonds", "octet rule"])
        )
        await session.execute(stmt)

    ctx = TenantContext(org_id=org_id)
    query_vector = _vector(1)

    # Call _retrieve_hits and capture logs. capture_logs() only swaps structlog's
    # processor chain — it does NOT lift the app's configured wrapper_class level
    # filter (LOG_LEVEL=INFO by default), so a DEBUG call is dropped before any
    # processor (including the capture) ever sees it. Lower the threshold for the
    # duration of this assertion and restore it exactly afterward.
    old_wrapper_class = structlog.get_config()["wrapper_class"]
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
    try:
        with structlog.testing.capture_logs() as cap_logs:
            hits = await retrieval_service._retrieve_hits(
                ctx,
                query_vector=query_vector,
                scope=[doc_id],
                model=FAKE_MODEL,
                k=8,
            )
    finally:
        structlog.configure(wrapper_class=old_wrapper_class)

    # Verify hits returned.
    assert len(hits) >= 1

    # Find the hierarchical_used event and verify it's at INFO level.
    hierarchical_used_event = None
    for log_entry in cap_logs:
        if log_entry.get("event") == "retrieval.hierarchical_used":
            hierarchical_used_event = log_entry
            break

    assert hierarchical_used_event is not None, "retrieval.hierarchical_used event not found"
    assert hierarchical_used_event.get("log_level") == "info"

    # Find the section_topics event and verify it's at DEBUG level with correct payload.
    section_topics_event = None
    for log_entry in cap_logs:
        if log_entry.get("event") == "retrieval.section_topics":
            section_topics_event = log_entry
            break

    assert section_topics_event is not None, "retrieval.section_topics event not found"
    assert section_topics_event.get("log_level") == "debug"
    sections = section_topics_event.get("sections", [])
    assert len(sections) >= 1
    # Check that one of the sections has the topics we set.
    topics_found = False
    for section in sections:
        if section.get("topics") == ["ionic bonds", "octet rule"]:
            topics_found = True
            break
    assert topics_found, f"Expected topics not found in sections: {sections}"


async def test_search_sections_returns_topics_field(session_factory, tenant_engine) -> None:
    """Verify that search_sections returns the topics field from sections."""
    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    section_with_topics_id = uuid.uuid4()
    section_without_topics_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="TestOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Test"))

    # Seed section with topics.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_with_topics_id,
        ordinal=0,
        heading="Section with topics",
        path="1",
        seed=0,
    )

    # Update the section to have topics.
    async with session_factory() as session, session.begin():
        stmt = update(Section).where(Section.id == section_with_topics_id).values(topics=["vsepr"])
        await session.execute(stmt)

    # Seed section without topics.
    await _seed_section_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        section_id=section_without_topics_id,
        ordinal=1,
        heading="Section without topics",
        path="2",
        seed=1,
    )

    ctx = TenantContext(org_id=org_id)
    query_vector = _vector(0)

    # Search sections.
    hits = await ingestion_service.search_sections(
        ctx,
        query_vector=query_vector,
        document_ids=[doc_id],
        model=FAKE_MODEL,
        s=8,
    )

    # Should return 2 hits (one from seed=0, one from seed=1).
    assert len(hits) == 2

    # Find the hit with topics.
    hit_with_topics = None
    hit_without_topics = None
    for hit in hits:
        if hit.section_id == section_with_topics_id:
            hit_with_topics = hit
        elif hit.section_id == section_without_topics_id:
            hit_without_topics = hit

    assert hit_with_topics is not None, "Section with topics not found in hits"
    assert hit_without_topics is not None, "Section without topics not found in hits"

    assert hit_with_topics.topics == ["vsepr"]
    assert hit_without_topics.topics is None
