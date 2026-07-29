"""P1 broad-query router + map-reduce (memory.md "P1 roadmap"): the query classifier,
the map-reduce pass over V2 enrichment section summaries, the additive
``citation_type="section"`` citation shape, the missing-enrichment/too-many-documents
fallback, and org-scoping on the two new repository methods
(``SectionRepository.list_for_documents``/``ChunkRepository.list_for_sections``).

Uses the FAKE LLM/embedder seams exclusively — deterministic, no network, no API keys.
``BROAD_QUERY_ENABLED`` defaults to False, so every existing test in ``test_chat.py``
already proves the gate-off path is otherwise untouched; this file focuses on the new
behavior plus one dedicated gate-off regression test mirroring the reranker feature's
precedent (``test_ask_weak_evidence_gate_never_fires_when_reranker_disabled``).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.chat import MessageTrace
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding, Section
from app.services.chat.broad_query import classify_query
from app.services.ingestion import ingestion_service
from app.services.ingestion.repository import ChunkRepository, SectionRepository
from app.services.retrieval.mapreduce import (
    SectionSummaryHit,
    build_synthesis_blocks,
    is_broad_query_available,
)
from app.services.seams import EMBED_DIM, Message, get_llm
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


def _vector(seed: int, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    vec[seed % dim] = 1.0
    return vec


class _BroadRoutingLLM:
    """Test double distinguishing the 3 distinct LLM call shapes broad-query issues
    (classify / map / reduce) by matching a distinguishing substring in the SYSTEM
    prompt — mirrors the precedent of writing a purpose-built fake per feature (e.g.
    ``_FixedScoreReranker``/``_RecordingLLM`` in ``test_chat.py``) rather than reusing
    the default context-aware ``FakeLLM``, which has no concept of these 3 shapes.
    Records call counts so tests can prove exactly which calls did/didn't happen."""

    def __init__(self, classify_verdict: str = "BROAD") -> None:
        self._classify_verdict = classify_verdict
        self.classify_calls = 0
        self.map_calls = 0
        self.reduce_calls = 0

    @property
    def model(self) -> str:
        return "broad-routing-llm"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        system = messages[0].content
        user = messages[1].content
        if "BROAD or SPECIFIC" in system:
            self.classify_calls += 1
            text = self._classify_verdict
        elif "extract information relevant to a stated purpose from one section" in system:
            self.map_calls += 1
            heading_line = next(
                (line for line in user.splitlines() if line.startswith("Section heading:")),
                "Section heading: ?",
            )
            text = f"extract about {heading_line.split(':', 1)[1].strip()}"
        else:
            self.reduce_calls += 1
            markers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", user)})
            text = "Synthesized answer " + " ".join(f"[{n}]" for n in markers)
        for token in text.split(" "):
            yield token + " "


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_llm, None)


async def _signup(client: AsyncClient, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org_name}
    )
    assert resp.status_code == 201
    return resp.json()


async def _org_id(client: AsyncClient, headers: dict) -> uuid.UUID:
    me = await client.get("/auth/me", headers=headers)
    return uuid.UUID(me.json()["org_id"])


async def _seed_document(session_factory, org_id: uuid.UUID, title: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title))
    return doc_id


async def _seed_section_with_summary(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    heading: str,
    summary: str,
    topics: list[str] | None = None,
    chunk_content: str = "chunk content",
    seed: int = 0,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seeds one ``Section`` carrying a V2 enrichment summary, plus one ``Chunk`` +
    ``Embedding`` under it — the exact shape ``SectionRepository.list_for_documents``
    reads (broad-query's input) and, incidentally, still gives the flat/chunk path
    something to find in the fallback tests."""
    section_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Section(
                id=section_id,
                org_id=org_id,
                document_id=document_id,
                parent_section_id=None,
                ordinal=0,
                depth=1,
                path="1",
                heading=heading,
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=len(chunk_content),
                summary=summary,
                topics=topics,
            )
        )
        await session.flush()  # Section must exist before the Chunk FK references it
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                section_id=section_id,
                ordinal=0,
                content=chunk_content,
                token_count=max(len(chunk_content) // 4, 1),
                char_start=0,
                char_end=len(chunk_content),
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=document_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model=FAKE_MODEL,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return section_id, chunk_id


async def _seed_chunk_without_section(
    session_factory, *, org_id: uuid.UUID, document_id: uuid.UUID, content: str, seed: int = 0
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                section_id=None,
                ordinal=0,
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
                model=FAKE_MODEL,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return chunk_id


# ---- HTTP-level routing tests ---------------------------------------------------------


async def test_ask_routes_to_broad_query_when_enabled_and_classified_broad(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full happy path: flag on, two sections carrying enrichment summaries, the
    classifier says BROAD -> map-reduce runs -> the synthesized answer's citations are
    additive ``citation_type="section"`` with ``section_id``/``heading`` populated and
    chunk fields null (never fabricated)."""
    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)

    tokens = await _signup(client, "broad-happy@test.com", "BroadHappy")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    section_a, _chunk_a = await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Onboarding",
        summary="This section covers onboarding steps.",
        topics=["onboarding"],
        chunk_content="onboarding content",
        seed=1,
    )
    section_b, _chunk_b = await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Billing",
        summary="This section covers billing concerns.",
        topics=["billing"],
        chunk_content="billing content",
        seed=2,
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    routing_llm = _BroadRoutingLLM()
    app.dependency_overrides[get_llm] = lambda: routing_llm

    resp = await client.post(
        "/chat/ask",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "what's the gist of everything?"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert routing_llm.classify_calls == 1
    assert routing_llm.map_calls == 2  # one per section
    assert routing_llm.reduce_calls == 1
    assert body["weak_evidence"] is False
    assert "[1]" in body["answer"] and "[2]" in body["answer"]

    citations = body["citations"]
    assert len(citations) == 2
    section_ids_seen = {c["section_id"] for c in citations}
    assert section_ids_seen == {str(section_a), str(section_b)}
    for citation in citations:
        assert citation["citation_type"] == "section"
        assert citation["chunk_id"] is None
        assert citation["char_start"] is None
        assert citation["char_end"] is None
        assert citation["heading"] in {"Onboarding", "Billing"}
        assert citation["document_id"] == str(doc_id)


async def test_ask_broad_query_persists_trace_with_synthesis_block_hits(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persisted ``message_traces.hits`` for a broad-query answer are
    ``SynthesisBlock``-shaped (``section_ids``/``headings``/``document_ids`` keys, no
    ``chunk_id`` key) — proving ``_persist``'s widened ``hits`` type hint actually
    round-trips the section-hit shape through the trace, not just the response body."""
    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)

    tokens = await _signup(client, "broad-trace@test.com", "BroadTrace")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    section_id, _chunk_id = await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Overview",
        summary="A broad overview summary.",
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    app.dependency_overrides[get_llm] = lambda: _BroadRoutingLLM()
    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "gist?"}
    )
    body = resp.json()

    async with session_factory() as session:
        stored = (
            await session.execute(
                select(MessageTrace).where(MessageTrace.message_id == uuid.UUID(body["message_id"]))
            )
        ).scalar_one()
    assert len(stored.hits) == 1
    hit = stored.hits[0]
    assert "chunk_id" not in hit
    assert hit["section_ids"] == [str(section_id)]
    assert hit["headings"] == ["Overview"]
    assert hit["document_ids"] == [str(doc_id)]

    # Also round-trips correctly through the admin trace endpoint (MessageTraceOut's
    # additive `list[ContextBlock] | list[SynthesisBlock]` union).
    trace_resp = await client.get(f"/chat/messages/{body['message_id']}/trace", headers=headers)
    assert trace_resp.status_code == 200
    trace_hit = trace_resp.json()["hits"][0]
    assert trace_hit["section_ids"] == [str(section_id)]


async def test_stream_broad_query_yields_only_done_event(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSE variant: zero ``token`` events (map-reduce doesn't stream incrementally),
    exactly one ``done`` event carrying the synthesized answer + section citations —
    same "zero tokens, one done" shape as the confidence gate's fire path."""
    import json

    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)

    tokens = await _signup(client, "broad-stream@test.com", "BroadStream")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Overview",
        summary="A broad overview summary.",
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    app.dependency_overrides[get_llm] = lambda: _BroadRoutingLLM()
    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "gist?"}
    )
    assert resp.status_code == 200

    events = [
        json.loads(block[6:])
        for block in resp.text.split("\n\n")
        if block.strip().startswith("data: ")
    ]
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert token_events == []
    assert len(done_events) == 1
    done = done_events[0]
    assert done["weak_evidence"] is False
    assert done["citations"][0]["citation_type"] == "section"
    assert "[1]" in done["answer"]


# ---- Fallback tests --------------------------------------------------------------------


async def test_ask_falls_back_when_zero_section_summaries(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BROAD_QUERY_ENABLED=True`` but the notebook's documents carry zero V2
    enrichment summaries: broad-query must NOT run at all — falls through to the
    existing flat/hybrid/rerank pipeline (citation_type defaults to "chunk"). The
    classifier is never even consulted (proven via ``classify_calls == 0``), matching
    the locked "log INFO, degrade, never raise" fallback shape."""
    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)

    tokens = await _signup(client, "broad-nosum@test.com", "BroadNoSum")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_without_section(
        session_factory, org_id=org_id, document_id=doc_id, content="alpha content here"
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    routing_llm = _BroadRoutingLLM()
    app.dependency_overrides[get_llm] = lambda: routing_llm

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert routing_llm.classify_calls == 0
    assert routing_llm.map_calls == 0
    # Falls through to the normal flat path -- the routing LLM's "reduce"-shaped branch
    # answers the flat prompt instead (proving generate_answer/call_llm_with_retry ran).
    assert routing_llm.reduce_calls == 1
    citation = body["citations"][0]
    assert citation["citation_type"] == "chunk"
    assert citation["chunk_id"] is not None
    assert citation["section_id"] is None


async def test_ask_falls_back_when_document_count_exceeds_max(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BROAD_QUERY_MAX_DOCUMENTS`` exceeded: even with valid section summaries on
    every document, broad-query must NOT run -- falls through unchanged."""
    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)
    monkeypatch.setattr(settings, "BROAD_QUERY_MAX_DOCUMENTS", 1)

    tokens = await _signup(client, "broad-toomany@test.com", "BroadTooMany")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    for i in range(2):
        doc_id = await _seed_document(session_factory, org_id, f"Doc{i}")
        await _seed_section_with_summary(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            heading=f"Heading{i}",
            summary=f"Summary {i}",
            chunk_content=f"content number {i}",
            seed=i + 1,
        )
        await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    routing_llm = _BroadRoutingLLM()
    app.dependency_overrides[get_llm] = lambda: routing_llm

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "content"}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert routing_llm.classify_calls == 0
    assert routing_llm.map_calls == 0
    citation = body["citations"][0]
    assert citation["citation_type"] == "chunk"


async def test_ask_falls_through_unchanged_when_flag_off(
    client: AsyncClient, session_factory
) -> None:
    """Gate-off regression (mirrors ``test_ask_weak_evidence_gate_never_fires_when_
    reranker_disabled``'s precedent): ``BROAD_QUERY_ENABLED`` defaults to False. Even
    with section summaries present AND a classifier double that WOULD say BROAD if ever
    asked, the classifier/map calls never fire -- the flat/hybrid/rerank path runs
    exactly as it did before this feature existed."""
    tokens = await _signup(client, "broad-off@test.com", "BroadOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Overview",
        summary="A broad overview summary.",
        chunk_content="alpha content about onboarding",
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    routing_llm = _BroadRoutingLLM()
    app.dependency_overrides[get_llm] = lambda: routing_llm

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert routing_llm.classify_calls == 0
    assert routing_llm.map_calls == 0
    assert routing_llm.reduce_calls == 1  # the one normal LLM call, unchanged
    assert body["citations"][0]["citation_type"] == "chunk"
    assert body["citations"][0]["chunk_id"] is not None


# ---- Unit tests: classifier, pure functions --------------------------------------------


async def test_classify_query_default_fake_llm_is_specific() -> None:
    """The default context-free ``FakeLLM`` always refuses on a context-free prompt
    (see its own docstring) -- its refusal string is not "BROAD", so
    ``classify_query`` correctly treats it as SPECIFIC, the safe default."""
    from app.services.seams import FakeLLM

    assert await classify_query("what's the gist of this?", llm=FakeLLM()) is False


async def test_classify_query_recognizes_broad_verdict() -> None:
    assert await classify_query("q", llm=_BroadRoutingLLM(classify_verdict="BROAD")) is True


async def test_classify_query_treats_malformed_verdict_as_specific() -> None:
    """Any response other than exactly "BROAD" -- including a malformed/empty one --
    is treated as SPECIFIC (the safe fallback), never raises."""
    assert await classify_query("q", llm=_BroadRoutingLLM(classify_verdict="")) is False
    assert await classify_query("q", llm=_BroadRoutingLLM(classify_verdict="maybe?")) is False


def test_is_broad_query_available_false_on_empty_document_scope() -> None:
    assert is_broad_query_available([], []) is False


def test_is_broad_query_available_false_on_zero_summaries() -> None:
    assert is_broad_query_available([uuid.uuid4()], []) is False


def test_is_broad_query_available_false_when_document_count_exceeds_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "BROAD_QUERY_MAX_DOCUMENTS", 1)
    hit = SectionSummaryHit(
        section_id=uuid.uuid4(), document_id=uuid.uuid4(), heading="H", summary="S"
    )
    assert is_broad_query_available([uuid.uuid4(), uuid.uuid4()], [hit]) is False


def test_is_broad_query_available_true_with_summaries_within_cap() -> None:
    hit = SectionSummaryHit(
        section_id=uuid.uuid4(), document_id=uuid.uuid4(), heading="H", summary="S"
    )
    assert is_broad_query_available([uuid.uuid4()], [hit]) is True


def test_build_synthesis_blocks_numbers_and_carries_provenance() -> None:
    section_a = SectionSummaryHit(
        section_id=uuid.uuid4(), document_id=uuid.uuid4(), heading="A", summary="Sa"
    )
    section_b = SectionSummaryHit(
        section_id=uuid.uuid4(), document_id=uuid.uuid4(), heading="B", summary="Sb"
    )
    blocks = build_synthesis_blocks([(section_a, "extract a"), (section_b, "extract b")])
    assert [b.index for b in blocks] == [1, 2]
    assert blocks[0].content == "extract a"
    assert blocks[0].section_ids == [section_a.section_id]
    assert blocks[0].headings == [section_a.heading]
    assert blocks[0].document_ids == [section_a.document_id]
    assert blocks[1].content == "extract b"


# ---- Org scoping on the new repository methods -----------------------------------------


async def test_list_section_summaries_org_scoped_returns_nothing_cross_org(
    session_factory, tenant_engine
) -> None:
    """``ingestion_service.list_section_summaries`` (backs
    ``SectionRepository.list_for_documents``) is org-scoped independently — calling it
    with org A's context but org B's document id must return nothing, mirroring
    ``test_search_chunks_org_id_is_an_independent_backstop`` in ``test_retrieval.py``."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        session.add(Document(id=uuid.uuid4(), org_id=org_a, title="A"))
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    await _seed_section_with_summary(
        session_factory,
        org_id=org_b,
        document_id=doc_b,
        heading="Secret",
        summary="org b secret summary",
    )

    ctx_a = TenantContext(org_id=org_a)
    hits = await ingestion_service.list_section_summaries(ctx_a, [doc_b])
    assert hits == []

    # control: org B's own context CAN see it, proving the absence above is the filter.
    ctx_b = TenantContext(org_id=org_b)
    control_hits = await ingestion_service.list_section_summaries(ctx_b, [doc_b])
    assert len(control_hits) == 1
    assert control_hits[0].summary == "org b secret summary"


async def test_section_repository_list_for_documents_org_scoped(
    session_factory, tenant_engine
) -> None:
    """Repository-level backstop (independent of the ``ingestion_service`` wrapper
    above) for ``SectionRepository.list_for_documents`` itself."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    section_id, _chunk_id = await _seed_section_with_summary(
        session_factory, org_id=org_b, document_id=doc_b, heading="H", summary="S"
    )

    async with session_factory() as session:
        rows_a = await SectionRepository(session, TenantContext(org_id=org_a)).list_for_documents(
            [doc_b]
        )
        assert rows_a == []

        rows_b = await SectionRepository(session, TenantContext(org_id=org_b)).list_for_documents(
            [doc_b]
        )
        assert [r.id for r in rows_b] == [section_id]


async def test_chunk_repository_list_for_sections_org_scoped(
    session_factory, tenant_engine
) -> None:
    """Org-scoping backstop for ``ChunkRepository.list_for_sections`` — built for
    completeness/future use (NOT called by the P1 broad-query path itself, which
    operates on section summaries alone; see ``mapreduce.py``'s module docstring)."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    section_id, chunk_id = await _seed_section_with_summary(
        session_factory, org_id=org_b, document_id=doc_b, heading="H", summary="S"
    )

    async with session_factory() as session:
        rows_a = await ChunkRepository(session, TenantContext(org_id=org_a)).list_for_sections(
            [section_id]
        )
        assert rows_a == []

        rows_b = await ChunkRepository(session, TenantContext(org_id=org_b)).list_for_sections(
            [section_id]
        )
        assert [r.id for r in rows_b] == [chunk_id]


async def test_ask_cross_org_document_never_leaks_into_broad_query_scope(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: broad-query's document scope comes from
    ``retrieval_service.resolve_notebook_scope`` (notebook ∩ allowed), the SAME
    org-scoped resolution the flat path already uses — a second org's document, even if
    it somehow carried a section summary, is never part of this org's notebook scope in
    the first place. Asserted via the ordinary cross-org 404 (notebook access itself is
    org-scoped), proving broad-query doesn't introduce a new path around that check."""
    monkeypatch.setattr(settings, "BROAD_QUERY_ENABLED", True)
    tokens_a = await _signup(client, "broad-isoa@test.com", "BroadIsoA")
    tokens_b = await _signup(client, "broad-isob@test.com", "BroadIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (
        await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    ).json()["id"]

    resp = await client.post(
        "/chat/ask", headers=headers_b, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 404
