"""F20 parsing stage integration tests."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.ingestion import Chunk, Embedding, Section
from app.services.queue import get_job_queue
from app.services.seams import ParsedDoc, get_embedder, get_parser
from app.services.storage import build_artifact_key, get_object_store
from main import app
from tests.conftest import FakeJobQueue


class _InMemoryObjectStore:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]


class _FailingParser:
    async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
        raise RuntimeError("OCR vendor unreachable")


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    store = _InMemoryObjectStore()
    app.dependency_overrides[get_object_store] = lambda: store
    # F24: upload now enqueues the parsing job — fake the queue so these tests never
    # touch real Redis/arq.
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_parser, None)
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


async def test_parse_document_moves_to_structuring(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-up1@test.com", "IngOne")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload(client, headers)

    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "STRUCTURING"
    assert body["language"] == "en"
    assert body["page_count"] == 1
    assert body["failed_stage"] is None
    assert body["error_detail"] is None

    artifact_key = build_artifact_key(uuid.UUID(doc["org_id"]), uuid.UUID(doc["id"]), "parsing")
    store = app.dependency_overrides[get_object_store]()
    artifact = json.loads(store.puts[artifact_key])
    assert artifact["language"] == "en"
    assert "outline" in artifact and len(artifact["outline"]) > 0


async def test_parse_failure_sets_failed_status(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-up2@test.com", "IngTwo")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload(client, headers)

    app.dependency_overrides[get_parser] = lambda: _FailingParser()
    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == "PARSING"
    assert "OCR vendor unreachable" in body["error_detail"]


async def test_reparsing_a_structured_document_is_idempotent(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-up3@test.com", "IngThree")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload(client, headers)

    first = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert first.json()["status"] == "STRUCTURING"

    second = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()


async def test_parse_document_tenant_isolation(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "ing-isoa@test.com", "IngIsoA")
    tokens_b = await _signup(client, "ing-isob@test.com", "IngIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    doc = await _upload(client, headers_a)

    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers_b)
    assert resp.status_code == 404


async def _upload_and_parse(client: AsyncClient, headers: dict) -> dict:
    doc = await _upload(client, headers)
    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert resp.json()["status"] == "STRUCTURING"
    return doc


async def test_structure_document_builds_sections_and_chunks(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-st1@test.com", "IngSt1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "EMBEDDING"
    assert body["failed_stage"] is None
    assert body["error_detail"] is None


async def test_structure_document_idempotent_rerun_no_duplicates(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-st2@test.com", "IngSt2")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    first = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert first.json()["status"] == "EMBEDDING"

    second = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()


async def test_structure_document_failure_sets_failed_status(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-st3@test.com", "IngSt3")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    store = app.dependency_overrides[get_object_store]()
    artifact_key = build_artifact_key(uuid.UUID(doc["org_id"]), uuid.UUID(doc["id"]), "parsing")
    del store.puts[artifact_key]  # simulate a missing/corrupt artifact

    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == "STRUCTURING"


async def test_structure_document_tenant_isolation(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "ing-isoc@test.com", "IngIsoC")
    tokens_b = await _signup(client, "ing-isod@test.com", "IngIsoD")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    doc = await _upload_and_parse(client, headers_a)

    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers_b)
    assert resp.status_code == 404


async def test_structure_document_sections_and_chunks_shape(
    client: AsyncClient, session_factory
) -> None:
    """FakeParser returns a flat two-heading outline (Introduction, Background) — both
    leaves, so each gets its own section and at least one chunk, every chunk carrying
    valid offsets and a section_id."""
    tokens = await _signup(client, "ing-st4@test.com", "IngSt4")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)
    doc_id = uuid.UUID(doc["id"])

    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.json()["status"] == "EMBEDDING"

    async with session_factory() as session:
        sections = list(await session.scalars(select(Section).where(Section.document_id == doc_id)))
        chunks = list(await session.scalars(select(Chunk).where(Chunk.document_id == doc_id)))

    assert {s.heading for s in sections} == {"Introduction", "Background"}
    assert all(s.parent_section_id is None for s in sections)
    assert len(chunks) >= len(sections)
    section_ids = {s.id for s in sections}
    for chunk in chunks:
        assert chunk.section_id in section_ids
        assert chunk.char_start < chunk.char_end


async def test_structure_document_degenerate_outline_one_root_section(
    client: AsyncClient, session_factory
) -> None:
    """architecture.md "Degenerate-outline contract": a headingless document gets ONE root
    section spanning the full char range, and every chunk attaches to it."""

    class _HeadinglessParser:
        async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
            text = "Plain text with no headings at all, just a wall of prose to chunk.\n"
            return ParsedDoc(text=text, outline=[], language="en", page_count=1)

    tokens = await _signup(client, "ing-st5@test.com", "IngSt5")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    app.dependency_overrides[get_parser] = lambda: _HeadinglessParser()
    doc = await _upload_and_parse(client, headers)
    doc_id = uuid.UUID(doc["id"])

    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.json()["status"] == "EMBEDDING"

    async with session_factory() as session:
        sections = list(await session.scalars(select(Section).where(Section.document_id == doc_id)))
        chunks = list(await session.scalars(select(Chunk).where(Chunk.document_id == doc_id)))

    assert len(sections) == 1
    root = sections[0]
    assert root.heading is None
    assert root.parent_section_id is None
    assert root.char_start == 0
    assert root.char_end > 0
    assert len(chunks) >= 1
    assert all(c.section_id == root.id for c in chunks)


class _FailingEmbedder:
    model = "failing-embed"
    dim = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embeddings API unreachable")


async def _upload_parse_and_structure(client: AsyncClient, headers: dict) -> dict:
    doc = await _upload_and_parse(client, headers)
    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.json()["status"] == "EMBEDDING"
    return doc


async def test_embed_document_moves_to_ready(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "ing-em1@test.com", "IngEm1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_and_structure(client, headers)
    doc_id = uuid.UUID(doc["id"])

    resp = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "READY"
    assert body["failed_stage"] is None
    assert body["error_detail"] is None

    async with session_factory() as session:
        chunks = list(await session.scalars(select(Chunk).where(Chunk.document_id == doc_id)))
        embeddings = list(
            await session.scalars(select(Embedding).where(Embedding.document_id == doc_id))
        )

    assert len(embeddings) == len(chunks)
    chunk_ids = {c.id for c in chunks}
    for emb in embeddings:
        assert emb.owner_type == "chunk"
        assert emb.owner_id in chunk_ids
        assert emb.model == "fake-embed-1536"
        assert emb.dim == 1536
        assert len(emb.embedding) == 1536


async def test_embed_document_failure_sets_failed_status(client: AsyncClient) -> None:
    tokens = await _signup(client, "ing-em2@test.com", "IngEm2")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_and_structure(client, headers)

    app.dependency_overrides[get_embedder] = lambda: _FailingEmbedder()
    resp = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == "EMBEDDING"
    assert "embeddings API unreachable" in body["error_detail"]


async def test_embed_document_idempotent_rerun_no_duplicates(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "ing-em3@test.com", "IngEm3")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_parse_and_structure(client, headers)
    doc_id = uuid.UUID(doc["id"])

    first = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
    assert first.json()["status"] == "READY"

    async with session_factory() as session:
        first_ids = {
            e.id
            for e in await session.scalars(select(Embedding).where(Embedding.document_id == doc_id))
        }

    second = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()

    async with session_factory() as session:
        second_rows = list(
            await session.scalars(select(Embedding).where(Embedding.document_id == doc_id))
        )

    assert {e.id for e in second_rows} == first_ids
    assert len(second_rows) == len(first_ids)


async def test_embed_document_tenant_isolation(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "ing-isoe@test.com", "IngIsoE")
    tokens_b = await _signup(client, "ing-isof@test.com", "IngIsoF")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    doc = await _upload_parse_and_structure(client, headers_a)

    resp = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers_b)
    assert resp.status_code == 404
