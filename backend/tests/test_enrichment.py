"""V2 enrichment stage integration tests."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.settings import settings
from app.models.ingestion import Embedding, Section
from app.services.queue import get_job_queue
from app.services.seams import Message, get_embedder, get_llm
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
