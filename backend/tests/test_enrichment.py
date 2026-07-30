"""V2 enrichment stage integration tests."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.settings import settings
from app.models.ingestion import Chunk, Embedding, Section
from app.services.queue import get_job_queue
from app.services.seams import FakeEmbedder, Message, get_embedder, get_llm
from app.services.storage import get_object_store
from main import app
from tests.conftest import FakeJobQueue


class _InMemoryObjectStore:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]


class _EnrichLLM:
    """Fake LLM that returns a valid enrichment response."""

    async def stream(self, messages: list[Message]):
        # Verify that messages are Message objects, not dicts.
        # This ensures the bug (dict-passing) is caught by tests.
        for msg in messages:
            _ = msg.role  # Access attribute to ensure it's a Message object
            _ = msg.content

        # Extract the heading from the user message if present.
        user_msg = next((m.content for m in messages if m.role == "user"), "")
        heading = "Unknown"
        if "Section heading:" in user_msg:
            heading = user_msg.split("Section heading:")[1].split("\n")[0].strip()

        response = json.dumps(
            {
                "summary": f"Summary of {heading}. This is a test summary.",
                "topics": ["topic1", "topic2"],
            }
        )
        for char in response:
            yield char


class _GarbageLLM:
    """Fake LLM that returns non-JSON garbage."""

    async def stream(self, messages: list[Message]):
        # Verify that messages are Message objects, not dicts.
        for msg in messages:
            _ = msg.role  # Access attribute to ensure it's a Message object
            _ = msg.content

        yield "This is not JSON at all!"


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    store = _InMemoryObjectStore()
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_llm, None)
    app.dependency_overrides.pop(get_embedder, None)
    app.dependency_overrides.pop(get_job_queue, None)


async def _signup(client: AsyncClient, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org_name}
    )
    assert resp.status_code == 201
    return resp.json()


async def _upload(client: AsyncClient, headers: dict) -> dict:
    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("handbook.pdf", b"hello world", "application/pdf")},
    )
    assert resp.status_code == 201
    return resp.json()


async def _upload_parse_structure_embed(client: AsyncClient, headers: dict) -> dict:
    """Helper: walk a document from upload through READY."""
    doc = await _upload(client, headers)

    # Parse
    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert resp.json()["status"] == "STRUCTURING"

    # Structure
    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.json()["status"] == "EMBEDDING"

    # Embed
    resp = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
    assert resp.json()["status"] == "READY"

    return doc


async def test_enrich_document_happy_path(client: AsyncClient, session_factory) -> None:
    """Enrichment adds summaries and section embeddings to a READY document."""
    tokens = await _signup(client, "enrich-hp@test.com", "EnrichHP")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    # Override LLM to return valid enrichment responses.
    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"  # Status unchanged

    # Verify sections have summaries and topics.
    async with session_factory() as session:
        sections = list(
            await session.scalars(
                select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
            )
        )
        # At least one section should have been enriched.
        assert len(sections) > 0
        enriched = [s for s in sections if s.summary is not None]
        assert len(enriched) > 0

        # Verify embeddings were created for enriched sections.
        section_ids = [s.id for s in enriched]
        embeddings = list(
            await session.scalars(
                select(Embedding).where(
                    Embedding.document_id == uuid.UUID(doc["id"]),
                    Embedding.owner_type == "section",
                    Embedding.owner_id.in_(section_ids),
                )
            )
        )
        assert len(embeddings) == len(enriched)


async def test_enrich_idempotent_rerun_no_dupes(client: AsyncClient, session_factory) -> None:
    """Re-enriching upserts embeddings without creating duplicates."""
    tokens = await _signup(client, "enrich-idem@test.com", "EnrichIdem")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # First enrichment.
    resp1 = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp1.status_code == 200

    async with session_factory() as session:
        first_count = len(
            list(
                await session.scalars(
                    select(Embedding).where(
                        Embedding.document_id == uuid.UUID(doc["id"]),
                        Embedding.owner_type == "section",
                    )
                )
            )
        )

    # Second enrichment (idempotent rerun).
    resp2 = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp2.status_code == 200

    async with session_factory() as session:
        second_count = len(
            list(
                await session.scalars(
                    select(Embedding).where(
                        Embedding.document_id == uuid.UUID(doc["id"]),
                        Embedding.owner_type == "section",
                    )
                )
            )
        )

    # Embedding row count should be unchanged (upsert, not insert).
    assert second_count == first_count


async def test_enrich_not_ready_no_op(client: AsyncClient) -> None:
    """Enriching a non-READY document is a no-op."""
    tokens = await _signup(client, "enrich-notready@test.com", "EnrichNotReady")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload(client, headers)

    # Document is at UPLOADED, not READY.
    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "UPLOADED"  # Unchanged


async def test_enrich_llm_garbage_no_error(client: AsyncClient) -> None:
    """LLM returning garbage (non-JSON) doesn't crash; section is skipped."""
    tokens = await _signup(client, "enrich-garbage@test.com", "EnrichGarbage")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _GarbageLLM()

    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    # Even with all sections failing, enrichment returns 200 and the document is READY.
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


async def test_enrich_cross_org_isolation(client: AsyncClient) -> None:
    """Enriching a document from another org returns 404."""
    tokens_a = await _signup(client, "enrich-isoa@test.com", "EnrichIsoA")
    tokens_b = await _signup(client, "enrich-isob@test.com", "EnrichIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    doc = await _upload_parse_structure_embed(client, headers_a)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # Try to enrich org A's document as org B.
    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers_b)
    assert resp.status_code == 404


async def test_enrich_chaining_enabled(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ENRICHMENT_ENABLED=True, embedding job enqueues enrichment job."""
    from app.services.ingestion.tasks import (
        run_embedding_stage_job,
        run_parsing_stage_job,
        run_structuring_stage_job,
    )

    # Temporarily enable enrichment.
    original = settings.ENRICHMENT_ENABLED
    try:
        settings.ENRICHMENT_ENABLED = True

        tokens = await _signup(client, "enrich-chain-on@test.com", "EnrichChainOn")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        doc = await _upload(client, headers)

        # Get the fake job queue and object store from the dependency overrides.
        job_queue: FakeJobQueue = app.dependency_overrides[get_job_queue]()
        store: _InMemoryObjectStore = app.dependency_overrides[get_object_store]()

        # Monkeypatch the get_object_store in tasks module so job functions use the fake store.
        monkeypatch.setattr("app.services.ingestion.tasks.get_object_store", lambda: store)

        # Clear the initial job_queue state (upload enqueued the parsing job).
        job_queue.calls.clear()

        org_id, document_id = doc["org_id"], doc["id"]

        # Run the job chain to completion, with enrichment enabled.
        await run_parsing_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )
        await run_structuring_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )
        await run_embedding_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )

        # Check that the enrichment job was enqueued by the embedding job.
        job_names = [call[0] for call in job_queue.calls]
        assert "run_enrichment_stage_job" in job_names

    finally:
        settings.ENRICHMENT_ENABLED = original


async def test_enrich_chaining_disabled(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ENRICHMENT_ENABLED=False (default), enrichment job is not enqueued."""
    from app.services.ingestion.tasks import (
        run_embedding_stage_job,
        run_parsing_stage_job,
        run_structuring_stage_job,
    )

    # Ensure enrichment is disabled.
    original = settings.ENRICHMENT_ENABLED
    try:
        settings.ENRICHMENT_ENABLED = False

        tokens = await _signup(client, "enrich-chain-off@test.com", "EnrichChainOff")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        doc = await _upload(client, headers)

        # Get the fake job queue and object store from the dependency overrides.
        job_queue: FakeJobQueue = app.dependency_overrides[get_job_queue]()
        store: _InMemoryObjectStore = app.dependency_overrides[get_object_store]()

        # Monkeypatch the get_object_store in tasks module so job functions use the fake store.
        monkeypatch.setattr("app.services.ingestion.tasks.get_object_store", lambda: store)

        # Clear the initial job_queue state (upload enqueued the parsing job).
        job_queue.calls.clear()

        org_id, document_id = doc["org_id"], doc["id"]

        # Run the job chain to completion, with enrichment disabled.
        await run_parsing_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )
        await run_structuring_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )
        await run_embedding_stage_job(
            {"job_queue": job_queue}, org_id=org_id, document_id=document_id
        )

        # Check that enrichment job was NOT enqueued.
        job_names = [call[0] for call in job_queue.calls]
        assert "run_enrichment_stage_job" not in job_names

    finally:
        settings.ENRICHMENT_ENABLED = original


async def test_enrich_chunk_embeddings_untouched(client: AsyncClient, session_factory) -> None:
    """Enrichment doesn't affect chunk embeddings."""
    tokens = await _signup(client, "enrich-chunk@test.com", "EnrichChunk")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # Count chunk embeddings before enrichment.
    async with session_factory() as session:
        before = len(
            list(
                await session.scalars(
                    select(Embedding).where(
                        Embedding.document_id == uuid.UUID(doc["id"]),
                        Embedding.owner_type == "chunk",
                    )
                )
            )
        )

    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp.status_code == 200

    # Count chunk embeddings after enrichment — should be unchanged.
    async with session_factory() as session:
        after = len(
            list(
                await session.scalars(
                    select(Embedding).where(
                        Embedding.document_id == uuid.UUID(doc["id"]),
                        Embedding.owner_type == "chunk",
                    )
                )
            )
        )

    assert after == before


# --- P1 contextual retrieval (memory.md "P1 roadmap") ---


class _FlakyEmbedder:
    """Wraps ``FakeEmbedder`` but raises when asked to embed a CONTEXTUALIZED chunk text
    (``summary + "\\n\\n" + chunk.content``) whose summary starts with ``fail_marker`` —
    never fails on a bare summary-only batch (the earlier section-embedding call), only
    the later per-section contextual chunk re-embed call. Used to prove one section's
    contextual re-embed failure doesn't break enrichment for other sections or the whole
    document."""

    def __init__(self, fail_marker: str) -> None:
        self._inner = FakeEmbedder()
        self._fail_marker = fail_marker

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dim(self) -> int:
        return self._inner.dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            if text.startswith(self._fail_marker) and "\n\n" in text:
                raise RuntimeError("simulated contextual re-embed failure")
        return await self._inner.embed(texts)


async def _chunk_embeddings_by_id(
    session_factory, document_id: str
) -> dict[uuid.UUID, list[float]]:
    """Fetches every ``owner_type='chunk'`` embedding vector for a document, keyed by
    chunk id."""
    async with session_factory() as session:
        embeddings = list(
            await session.scalars(
                select(Embedding).where(
                    Embedding.document_id == uuid.UUID(document_id),
                    Embedding.owner_type == "chunk",
                )
            )
        )
        return {emb.owner_id: list(emb.embedding) for emb in embeddings}


async def _chunks_by_id(session_factory, document_id: str) -> dict[uuid.UUID, Chunk]:
    async with session_factory() as session:
        chunks = list(
            await session.scalars(select(Chunk).where(Chunk.document_id == uuid.UUID(document_id)))
        )
        return {c.id: c for c in chunks}


async def test_contextual_embedding_disabled_by_default_chunk_vectors_unchanged(
    client: AsyncClient, session_factory
) -> None:
    """CONTEXTUAL_EMBEDDING_ENABLED defaults False — enrichment must leave every chunk's
    embedding vector byte-identical to its pre-enrichment, context-free value. This is a
    real regression test (compares actual vector VALUES), not merely a count check like
    ``test_enrich_chunk_embeddings_untouched``."""
    tokens = await _signup(client, "ctxemb-off@test.com", "CtxEmbOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    before = await _chunk_embeddings_by_id(session_factory, doc["id"])
    assert len(before) > 0

    assert settings.CONTEXTUAL_EMBEDDING_ENABLED is False  # confirm the default
    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
    assert resp.status_code == 200

    after = await _chunk_embeddings_by_id(session_factory, doc["id"])
    assert after.keys() == before.keys()
    for chunk_id, vector in before.items():
        assert after[chunk_id] == vector


async def test_contextual_embedding_enabled_reembeds_with_section_summary(
    client: AsyncClient, session_factory
) -> None:
    """With the flag on, every chunk's embedding is replaced IN PLACE with a vector
    derived from ``section.summary + "\\n\\n" + chunk.content`` — proven two ways: (1)
    the vector differs from the original context-free embedding, and (2) the new vector
    exactly matches what ``FakeEmbedder`` produces for the known contextualized text
    (not just ANY different vector — the actual contextualized text was used)."""
    tokens = await _signup(client, "ctxemb-on@test.com", "CtxEmbOn")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    before = await _chunk_embeddings_by_id(session_factory, doc["id"])
    chunks_before = await _chunks_by_id(session_factory, doc["id"])

    original = settings.CONTEXTUAL_EMBEDDING_ENABLED
    try:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = True
        app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

        resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
        assert resp.status_code == 200

        async with session_factory() as session:
            sections = list(
                await session.scalars(
                    select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
                )
            )
        summary_by_section_id = {s.id: s.summary for s in sections}

        after = await _chunk_embeddings_by_id(session_factory, doc["id"])
        assert after.keys() == before.keys()

        fake = FakeEmbedder()
        any_reembedded = False
        for chunk_id, vector in after.items():
            chunk = chunks_before[chunk_id]
            if chunk.section_id is None or summary_by_section_id.get(chunk.section_id) is None:
                continue
            summary = summary_by_section_id[chunk.section_id]
            (expected,) = await fake.embed([f"{summary}\n\n{chunk.content}"])
            # pgvector stores `embedding` as single-precision float4, so a value read back
            # after a round-trip through Postgres never exactly equals the full-precision
            # float64 `expected` computed fresh in Python — approx (not ==) is correct here.
            assert vector == pytest.approx(expected, abs=1e-6)
            assert vector != before[chunk_id]
            any_reembedded = True

        assert any_reembedded, "expected at least one chunk to be contextually re-embedded"
    finally:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = original


async def test_contextual_embedding_one_section_failure_does_not_break_others(
    client: AsyncClient, session_factory
) -> None:
    """A per-section contextual re-embed failure (simulated for the "Background" section)
    must not prevent the "Introduction" section's chunks from being contextually
    re-embedded, and must not fail the document/enrichment call as a whole."""
    tokens = await _signup(client, "ctxemb-partial@test.com", "CtxEmbPartial")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    before = await _chunk_embeddings_by_id(session_factory, doc["id"])
    chunks_before = await _chunks_by_id(session_factory, doc["id"])

    original = settings.CONTEXTUAL_EMBEDDING_ENABLED
    try:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = True
        app.dependency_overrides[get_llm] = lambda: _EnrichLLM()
        app.dependency_overrides[get_embedder] = lambda: _FlakyEmbedder("Summary of Background")

        resp = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "READY"  # document unaffected by the failure

        async with session_factory() as session:
            sections = list(
                await session.scalars(
                    select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
                )
            )
        # Both sections still got their summary/topics (that part is unaffected by the
        # contextual re-embed failure — it runs strictly after summary computation).
        assert all(s.summary is not None for s in sections)
        section_by_heading = {s.heading: s for s in sections}
        intro_section = section_by_heading["Introduction"]
        bg_section = section_by_heading["Background"]

        after = await _chunk_embeddings_by_id(session_factory, doc["id"])

        intro_reembedded = False
        for chunk_id, chunk in chunks_before.items():
            if chunk.section_id == intro_section.id:
                assert after[chunk_id] != before[chunk_id]
                intro_reembedded = True
            elif chunk.section_id == bg_section.id:
                # Background's contextual re-embed failed — its chunk embeddings stay
                # exactly as they were before this enrichment run.
                assert after[chunk_id] == before[chunk_id]

        assert intro_reembedded, "expected Introduction's chunks to be contextually re-embedded"
    finally:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = original
        app.dependency_overrides.pop(get_embedder, None)


async def test_contextual_embedding_idempotent_rerun(client: AsyncClient, session_factory) -> None:
    """Re-running enrichment with the flag on twice upserts the same chunk embedding rows
    in place — same row count, same final vectors, no duplication or corruption."""
    tokens = await _signup(client, "ctxemb-idem@test.com", "CtxEmbIdem")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_structure_embed(client, headers)

    original = settings.CONTEXTUAL_EMBEDDING_ENABLED
    try:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = True
        app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

        resp1 = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
        assert resp1.status_code == 200
        first = await _chunk_embeddings_by_id(session_factory, doc["id"])

        resp2 = await client.post(f"/ingestion/documents/{doc['id']}/enrich", headers=headers)
        assert resp2.status_code == 200
        second = await _chunk_embeddings_by_id(session_factory, doc["id"])

        assert second.keys() == first.keys()  # same row count, same chunk ids — no dupes
        for chunk_id, vector in first.items():
            assert second[chunk_id] == vector  # same content re-embedded -> same vector
    finally:
        settings.CONTEXTUAL_EMBEDDING_ENABLED = original
