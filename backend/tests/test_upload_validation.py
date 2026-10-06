"""U6: PDF-only upload validation + user-facing ingestion error messages."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.documents import DocumentStatus
from app.services.documents.documents import is_supported_upload
from app.services.ingestion.errors import (
    EMBEDDING_FAILED,
    NO_TEXT,
    PASSWORD_PROTECTED,
    SERVICE_UNAVAILABLE,
    UNREADABLE_PDF,
    UNSUPPORTED_TYPE,
    user_facing_error,
)
from app.services.queue import get_job_queue
from app.services.seams import SeamTransientError
from app.services.storage import get_object_store
from main import app
from tests.conftest import FakeJobQueue


class _Store:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]

    async def delete(self, key: str) -> None:
        self.puts.pop(key, None)


@pytest.fixture
async def store() -> _Store:
    return _Store()


@pytest.fixture
async def client(session_factory, tenant_engine, store: _Store) -> AsyncClient:
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_job_queue, None)


async def _headers(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": email}
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_non_pdf_upload_is_rejected_with_415_and_nothing_stored(
    client: AsyncClient, store: _Store
) -> None:
    headers = await _headers(client, "upval-a@test.com")
    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"] == "Only PDF files can be uploaded. Please choose a .pdf file."
    assert store.puts == {}
    listed = await client.get("/documents", headers=headers)
    assert listed.json() == []


async def test_pdf_with_generic_mime_is_accepted_and_dedupe_still_works(
    client: AsyncClient,
) -> None:
    headers = await _headers(client, "upval-b@test.com")
    file = {"file": ("report.PDF", b"%PDF-1.4 same bytes", "application/octet-stream")}
    first = await client.post("/documents/upload", headers=headers, files=file)
    assert first.status_code == 201
    second = await client.post("/documents/upload", headers=headers, files=file)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.parametrize(
    ("filename", "mime", "ok"),
    [
        ("a.pdf", "application/pdf", True),
        ("a", "application/pdf", True),
        ("a.pdf", "application/x-pdf", True),
        ("a.pdf", "application/octet-stream", True),
        ("a.pdf", "application/pdf; charset=binary", True),
        ("a.txt", "text/plain", False),
        ("a.doc", "application/msword", False),
        ("a.txt", "application/octet-stream", False),
        ("a.pdf", "text/plain", False),
    ],
)
def test_is_supported_upload(filename: str, mime: str, ok: bool) -> None:
    assert is_supported_upload(filename, mime) is ok


@pytest.mark.parametrize(
    ("stage", "exc", "expected"),
    [
        (
            DocumentStatus.PARSING,
            ValueError("RealParser supports PDF only, got mime='text/plain'; DOCX is a future"),
            UNSUPPORTED_TYPE,
        ),
        (
            DocumentStatus.PARSING,
            ValueError("RealParser: encrypted PDFs are not supported."),
            PASSWORD_PROTECTED,
        ),
        (
            DocumentStatus.PARSING,
            RuntimeError("RealParser: both cloudflare-ai and mistral-ocr returned negligible text"),
            NO_TEXT,
        ),
        (DocumentStatus.PARSING, Exception("Stream has ended unexpectedly"), UNREADABLE_PDF),
        (DocumentStatus.PARSING, SeamTransientError("timeout"), SERVICE_UNAVAILABLE),
        (DocumentStatus.EMBEDDING, RuntimeError("embeddings API unreachable"), EMBEDDING_FAILED),
    ],
)
def test_user_facing_error_never_leaks_raw_text(
    stage: DocumentStatus, exc: Exception, expected: str
) -> None:
    message = user_facing_error(stage, exc)
    assert message == expected
    for internal in ("RealParser", "F23", "cloudflare", "Stream has ended"):
        assert internal not in message
