"""F23 — opt-in validation of the real OpenRouter parser against an actual PDF.

NOT part of the offline Testcontainers CI suite (registered under the `real_parser`
marker, excluded explicitly in `.github/workflows/ci.yml`, and self-skips below anyway).
Run manually:

    OPENROUTER_API_KEY=sk-or-... REAL_PDF_PATH=/path/to/doc.pdf pytest -m real_parser -s \
        tests/test_real_parser_integration.py

Runs the full core path (upload -> parse -> structure -> embed) with the REAL parser and
the fake embedder (no embedder/LLM cost needed to validate the parser contract), then
prints the section tree, chunk offsets, which engine was used, and the two known F23
findings this is designed to surface empirically per document: whether heading structure
was recovered, and whether page-level provenance survived (it never does, today).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.ingestion.models import Chunk, Section
from app.platform.seams import RealParser, get_embedder, get_parser
from app.platform.storage import get_object_store
from main import app

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
REAL_PDF_PATH = os.environ.get("REAL_PDF_PATH")

pytestmark = [
    pytest.mark.real_parser,
    pytest.mark.skipif(
        not (OPENROUTER_API_KEY and REAL_PDF_PATH),
        reason="set OPENROUTER_API_KEY and REAL_PDF_PATH to run this opt-in F23 check",
    ),
]


class _InMemoryObjectStore:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    store = _InMemoryObjectStore()
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_parser] = lambda: RealParser()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_parser, None)
    app.dependency_overrides.pop(get_embedder, None)


async def test_real_pdf_reaches_ready_and_prints_structure_findings(
    client: AsyncClient, session_factory
) -> None:
    pdf_bytes = Path(REAL_PDF_PATH).read_bytes()

    resp = await client.post(
        "/auth/signup",
        json={"email": "f23-real-parser@test.com", "password": "password123", "org_name": "F23"},
    )
    assert resp.status_code == 201
    tokens = resp.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": (Path(REAL_PDF_PATH).name, pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201
    doc = resp.json()

    with structlog.testing.capture_logs() as logs:
        resp = await client.post(f"/ingestion/documents/{doc['id']}/parse", headers=headers)
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["status"] == "STRUCTURING", doc

        resp = await client.post(f"/ingestion/documents/{doc['id']}/structure", headers=headers)
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["status"] == "EMBEDDING", doc

        resp = await client.post(f"/ingestion/documents/{doc['id']}/embed", headers=headers)
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["status"] == "READY", doc

    engine_log = next((e for e in logs if e.get("event") == "seams.real_parser_extracted"), None)

    async with session_factory() as session:
        sections = (
            await session.scalars(select(Section).where(Section.document_id == doc["id"]))
        ).all()
        chunks = (await session.scalars(select(Chunk).where(Chunk.document_id == doc["id"]))).all()

    headings_recovered = any(s.heading is not None for s in sections)
    page_provenance_survived = any(
        s.page_start != 1 or s.page_end != doc["page_count"] for s in sections
    )

    print("\n--- F23 real-parser integration result ---")
    print(
        f"document status: {doc['status']}, language: {doc['language']}, pages: {doc['page_count']}"
    )
    print(f"engine used: {engine_log['engine'] if engine_log else 'unknown'}")
    print(f"headings recovered: {'yes' if headings_recovered else 'no'}")
    print(f"page-level provenance survived: {'yes' if page_provenance_survived else 'no'}")
    print(f"section tree ({len(sections)} sections):")
    for s in sorted(sections, key=lambda s: s.path):
        print(
            f"  [{s.path}] depth={s.depth} heading={s.heading!r} "
            f"chars=({s.char_start},{s.char_end})"
        )
    print(f"chunks ({len(chunks)}):")
    for c in sorted(chunks, key=lambda c: c.ordinal):
        print(
            f"  #{c.ordinal} section={c.section_id} chars=({c.char_start},{c.char_end}) "
            f"tokens~{c.token_count}"
        )
