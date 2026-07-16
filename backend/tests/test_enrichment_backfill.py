"""Enrichment backfill endpoint tests."""

from __future__ import annotations

import json
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.ingestion import Embedding, Section
from app.services.queue import get_job_queue
from app.services.seams import Message, get_embedder, get_llm
from app.services.storage import get_object_store
from app.utils.constants import ROLE_MEMBER
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


async def _upload(client: AsyncClient, headers: dict, content: bytes = b"hello world") -> dict:
    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("handbook.pdf", content, "application/pdf")},
    )
    assert resp.status_code in (200, 201)  # 201 on first upload, 200 on dedupe
    return resp.json()


async def _upload_parse_structure_embed(
    client: AsyncClient, headers: dict, content: bytes | None = None
) -> dict:
    """Helper: walk a document from upload through READY."""
    if content is None:
        # Generate unique content based on timestamp to avoid dedupe.
        content = f"hello world {time.time()}".encode()
    doc = await _upload(client, headers, content)

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


async def test_backfill_enriches_multiple_ready_documents(
    client: AsyncClient, session_factory
) -> None:
    """Backfill enriches all READY documents in the org."""
    tokens = await _signup(client, "enrichbf-multi@test.com", "EnrichBFMulti")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Upload and ingest two documents to READY.
    doc1 = await _upload_parse_structure_embed(client, headers)
    doc2 = await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # Call the backfill endpoint.
    resp = await client.post("/ingestion/enrich-backfill", headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["enriched"] == 2
    assert result["skipped"] == 0
    assert result["failed"] == 0

    # Verify both documents have enriched sections.
    async with session_factory() as session:
        for doc_id in [doc1["id"], doc2["id"]]:
            sections = list(
                await session.scalars(
                    select(Section).where(Section.document_id == uuid.UUID(doc_id))
                )
            )
            enriched = [s for s in sections if s.summary is not None]
            assert len(enriched) > 0


async def test_backfill_skips_non_ready_documents(client: AsyncClient) -> None:
    """Backfill skips non-READY documents and only enriches READY ones."""
    tokens = await _signup(client, "enrichbf-skip@test.com", "EnrichBFSkip")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Upload one document but leave it at UPLOADED (don't parse it).
    await _upload(client, headers)

    # Upload and ingest another to READY.
    await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # Call backfill.
    resp = await client.post("/ingestion/enrich-backfill", headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["enriched"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0


async def test_backfill_idempotent_rerun(client: AsyncClient, session_factory) -> None:
    """Re-running backfill is safe; embeddings are upserted, not duplicated."""
    tokens = await _signup(client, "enrichbf-idem@test.com", "EnrichBFIdem")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    doc = await _upload_parse_structure_embed(client, headers)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # First backfill run.
    resp1 = await client.post("/ingestion/enrich-backfill", headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["enriched"] == 1

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

    # Second backfill run (idempotent).
    resp2 = await client.post("/ingestion/enrich-backfill", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["enriched"] == 1

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


async def test_backfill_requires_admin(client: AsyncClient) -> None:
    """Backfill endpoint requires admin role."""
    # Sign up an owner (admin).
    tokens_owner = await _signup(client, "enrichbf-admin@test.com", "EnrichBFAdmin")
    headers_owner = {"Authorization": f"Bearer {tokens_owner['access_token']}"}

    # Invite a member with plain "member" role.
    invite = await client.post(
        "/auth/invite",
        headers=headers_owner,
        json={"email": "enrichbf-member@test.com", "password": "password123", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201

    # Login as the member to get their tokens.
    login_resp = await client.post(
        "/auth/login", json={"email": "enrichbf-member@test.com", "password": "password123"}
    )
    assert login_resp.status_code == 200
    tokens_member = login_resp.json()
    headers_member = {"Authorization": f"Bearer {tokens_member['access_token']}"}

    # Try to call backfill as a member — should get 403.
    resp = await client.post("/ingestion/enrich-backfill", headers=headers_member)
    assert resp.status_code == 403

    # Verify that the owner CAN call it.
    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()
    resp = await client.post("/ingestion/enrich-backfill", headers=headers_owner)
    assert resp.status_code == 200


async def test_backfill_is_org_scoped(client: AsyncClient, session_factory) -> None:
    """Backfill only enriches documents in the calling org."""
    # Create org A and upload+ingest a document.
    tokens_a = await _signup(client, "enrichbf-orga@test.com", "EnrichBFOrgA")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    doc_a = await _upload_parse_structure_embed(client, headers_a)

    # Create org B and upload+ingest a document.
    tokens_b = await _signup(client, "enrichbf-orgb@test.com", "EnrichBFOrgB")
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    doc_b = await _upload_parse_structure_embed(client, headers_b)

    app.dependency_overrides[get_llm] = lambda: _EnrichLLM()

    # Run backfill as org A.
    resp_a = await client.post("/ingestion/enrich-backfill", headers=headers_a)
    assert resp_a.status_code == 200
    result_a = resp_a.json()
    assert result_a["enriched"] == 1

    # Verify org A's document is enriched.
    async with session_factory() as session:
        sections_a = list(
            await session.scalars(
                select(Section).where(Section.document_id == uuid.UUID(doc_a["id"]))
            )
        )
        enriched_a = [s for s in sections_a if s.summary is not None]
        assert len(enriched_a) > 0

    # Verify org B's document was NOT enriched (sections still have no summary).
    async with session_factory() as session:
        sections_b = list(
            await session.scalars(
                select(Section).where(Section.document_id == uuid.UUID(doc_b["id"]))
            )
        )
        enriched_b = [s for s in sections_b if s.summary is not None]
        assert len(enriched_b) == 0
