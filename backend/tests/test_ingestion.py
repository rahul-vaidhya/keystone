"""F20 parsing stage integration tests."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.platform.seams import ParsedDoc, get_parser
from app.platform.storage import build_artifact_key, get_object_store
from main import app


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_parser, None)


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
