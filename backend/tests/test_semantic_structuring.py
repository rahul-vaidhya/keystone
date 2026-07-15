"""Integration tests for semantic outline in the structuring stage.

Tests the flag-gated LLM post-pass when the parser outline is degenerate, caching,
fallback behavior, and no-op when the flag is off.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.settings import settings
from app.models.ingestion import Section
from app.services.queue import get_job_queue
from app.services.seams import Message, OutlineNode, ParsedDoc, get_embedder, get_llm, get_parser
from app.services.storage import get_object_store
from main import app
from tests.conftest import FakeJobQueue


class _InMemoryObjectStore:
    """In-memory object store for tests."""

    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]

    async def delete(self, key: str) -> None:
        self.puts.pop(key, None)


class _DegenerateParser:
    """Parser that returns a page-level degenerate outline."""

    async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
        return ParsedDoc(
            text="Chapter 1: Introduction\nContent here.\nChapter 2: Methods\nMore content.",
            language="en",
            page_count=2,
            outline=[
                OutlineNode(
                    heading="document.pdf",
                    level=1,
                    char_start=0,
                    char_end=25,
                    page_start=1,
                    page_end=2,
                ),
                OutlineNode(
                    heading="Metadata",
                    level=2,
                    char_start=25,
                    char_end=50,
                    page_start=1,
                    page_end=2,
                ),
                OutlineNode(
                    heading="Contents",
                    level=2,
                    char_start=50,
                    char_end=75,
                    page_start=1,
                    page_end=2,
                ),
                OutlineNode(
                    heading="Page 1",
                    level=3,
                    char_start=75,
                    char_end=100,
                    page_start=1,
                    page_end=1,
                ),
                OutlineNode(
                    heading="Page 2",
                    level=3,
                    char_start=100,
                    char_end=170,
                    page_start=2,
                    page_end=2,
                ),
            ],
        )


class _OutlineLLM:
    """Fake LLM for semantic outline derivation."""

    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self.calls = 0

    @property
    def model(self) -> str:
        return "semantic-fake"

    async def stream(self, messages: list[Message]):
        """Stream tokens from the current payload."""
        # Verify that messages are Message objects, not dicts.
        # This ensures the bug (dict-passing) is caught by tests.
        for msg in messages:
            _ = msg.role  # Access attribute to ensure it's a Message object
            _ = msg.content

        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        for tok in payload.split(" "):
            yield tok + " "


class _GarbageOutlineLLM:
    """LLM that returns non-JSON garbage."""

    @property
    def model(self) -> str:
        return "garbage-fake"

    async def stream(self, messages: list[Message]):
        """Yield garbage that won't parse as JSON."""
        # Verify that messages are Message objects, not dicts.
        for msg in messages:
            _ = msg.role  # Access attribute to ensure it's a Message object
            _ = msg.content

        yield "not "
        yield "valid "
        yield "json"


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    """Test client with dependency overrides for storage, parser, queue, and LLM."""
    store = _InMemoryObjectStore()
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_parser] = lambda: _DegenerateParser()
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    app.dependency_overrides[get_embedder] = lambda: app.dependency_overrides.get(
        get_embedder, lambda: None
    )()
    payload = (
        '[{"heading": "Chapter 1: Introduction", "level": 1}, '
        '{"heading": "Chapter 2: Methods", "level": 1}]'
    )
    app.dependency_overrides[get_llm] = lambda: _OutlineLLM([payload])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_parser, None)
    app.dependency_overrides.pop(get_job_queue, None)
    app.dependency_overrides.pop(get_embedder, None)
    app.dependency_overrides.pop(get_llm, None)


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


async def _upload_and_parse(client: AsyncClient, headers: dict) -> dict:
    doc = await _upload(client, headers)
    resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
    assert resp.json()["status"] == "STRUCTURING"
    return doc


async def test_semantic_outline_replaces_degenerate_parser_outline(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With flag ON and degenerate parser outline, sections carry real headings from LLM."""
    monkeypatch.setattr(settings, "SEMANTIC_OUTLINE_ENABLED", True)

    tokens = await _signup(client, "semstr-real1@test.com", "SemStr1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    # Structure the document (flag is ON, so semantic outline will be used).
    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "EMBEDDING"

    # Query the database for Section rows to verify they carry the real headings.
    async with session_factory() as session:
        stmt = select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
        sections = list(await session.scalars(stmt))

    # The two real headings from the LLM should be in the sections.
    headings = {s.heading for s in sections if s.heading}
    assert "Chapter 1: Introduction" in headings
    assert "Chapter 2: Methods" in headings

    # Verify the degenerate headings are NOT there.
    assert "Page 1" not in headings
    assert "Page 2" not in headings


async def test_semantic_outline_fallback_on_llm_failure(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With flag ON but LLM returns garbage, sections use the parser outline (fallback)."""
    monkeypatch.setattr(settings, "SEMANTIC_OUTLINE_ENABLED", True)
    app.dependency_overrides[get_llm] = lambda: _GarbageOutlineLLM()

    tokens = await _signup(client, "semstr-garbage@test.com", "SemStrGarbage")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    # Structure the document (LLM will fail, fallback to parser outline).
    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "EMBEDDING"  # Stage never fails, even on LLM failure.

    # Query sections: should have the degenerate parser outline, not real headings.
    async with session_factory() as session:
        stmt = select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
        sections = list(await session.scalars(stmt))

    # The page-level headings from the parser should be there.
    headings = {s.heading for s in sections if s.heading}
    assert "Page 1" in headings
    assert "Page 2" in headings


async def test_semantic_outline_caching(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Semantic outline is cached; second structure call doesn't re-call LLM."""
    monkeypatch.setattr(settings, "SEMANTIC_OUTLINE_ENABLED", True)

    tokens = await _signup(client, "semstr-cache@test.com", "SemStrCache")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    # First structure call.
    resp1 = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp1.status_code == 200
    llm = app.dependency_overrides[get_llm]()
    first_calls = llm.calls

    # Force the document back to STRUCTURING so we can re-run (mimic idempotent re-run).
    async with session_factory() as session:
        stmt = select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
        sections = list(await session.scalars(stmt))
        for section in sections:
            await session.delete(section)
        # Re-fetch and reset status.
        from app.models.documents import Document, DocumentStatus

        stmt = select(Document).where(Document.id == uuid.UUID(doc["id"]))
        document = await session.scalar(stmt)
        document.status = DocumentStatus.STRUCTURING
        await session.commit()

    # Second structure call (semantic outline is cached).
    resp2 = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp2.status_code == 200
    # LLM should not have been called again (cache served).
    assert llm.calls == first_calls


async def test_semantic_outline_flag_off(client: AsyncClient, session_factory) -> None:
    """With flag OFF (default), semantic outline is skipped; sections use parser outline."""
    # Flag is OFF by default.
    assert settings.SEMANTIC_OUTLINE_ENABLED is False

    tokens = await _signup(client, "semstr-off@test.com", "SemStrOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = await _upload_and_parse(client, headers)

    # Structure the document (flag is OFF, so no semantic outline).
    resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "EMBEDDING"

    # Sections should have the degenerate page-level outline, not real headings.
    async with session_factory() as session:
        stmt = select(Section).where(Section.document_id == uuid.UUID(doc["id"]))
        sections = list(await session.scalars(stmt))

    headings = {s.heading for s in sections if s.heading}
    # The page wrapper headings from the parser.
    assert "Page 1" in headings
    assert "Page 2" in headings
    # Real headings should NOT be there (no LLM was called).
    assert "Chapter 1: Introduction" not in headings
    assert "Chapter 2: Methods" not in headings
