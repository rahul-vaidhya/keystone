"""F11 folders + tags, F12 upload + dedupe integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.documents.models import Document
from app.platform.storage import get_object_store
from main import app


class _InMemoryObjectStore:
    """F12 test double: object storage isn't a seam (architecture.md — only Parser/
    Embedder/LLM are), so this is plain FastAPI dependency-override DI, not a 4th seam."""

    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    app.dependency_overrides[get_object_store] = lambda: _InMemoryObjectStore()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)


async def _signup(client: AsyncClient, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org_name}
    )
    assert resp.status_code == 201
    return resp.json()


async def test_create_nested_folders_and_list(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-owner1@test.com", "Acme")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    root = await client.post("/documents/folders", headers=headers, json={"name": "HR"})
    assert root.status_code == 201
    root_body = root.json()
    assert root_body["path"] == "HR"

    child = await client.post(
        "/documents/folders",
        headers=headers,
        json={"name": "Policies", "parent_id": root_body["id"]},
    )
    assert child.status_code == 201
    child_body = child.json()
    assert child_body["path"] == "HR/Policies"
    assert child_body["parent_id"] == root_body["id"]

    listing = await client.get("/documents/folders", headers=headers)
    assert listing.status_code == 200
    paths = {f["path"] for f in listing.json()}
    assert paths == {"HR", "HR/Policies"}


async def test_create_folder_missing_parent_404(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-owner2@test.com", "Beta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/documents/folders", headers=headers, json={"name": "X", "parent_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


async def test_delete_folder(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-owner6@test.com", "Zeta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    folder = await client.post("/documents/folders", headers=headers, json={"name": "Temp"})
    folder_id = folder.json()["id"]

    resp = await client.delete(f"/documents/folders/{folder_id}", headers=headers)
    assert resp.status_code == 204

    missing = await client.get(f"/documents/folders/{folder_id}", headers=headers)
    assert missing.status_code == 404


async def test_tag_a_document_and_list_by_tag(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "docs-owner3@test.com", "Gamma")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/auth/me", headers=headers)
    org_id = uuid.UUID(me.json()["org_id"])

    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title="Handbook"))

    tag = await client.post("/documents/tags", headers=headers, json={"name": "important"})
    assert tag.status_code == 201
    tag_id = tag.json()["id"]

    attach = await client.post(f"/documents/{doc_id}/tags/{tag_id}", headers=headers)
    assert attach.status_code == 204

    by_tag = await client.get("/documents", headers=headers, params={"tag_id": tag_id})
    assert by_tag.status_code == 200
    assert [d["title"] for d in by_tag.json()] == ["Handbook"]

    detach = await client.delete(f"/documents/{doc_id}/tags/{tag_id}", headers=headers)
    assert detach.status_code == 204

    by_tag_after = await client.get("/documents", headers=headers, params={"tag_id": tag_id})
    assert by_tag_after.json() == []


async def test_list_documents_by_folder(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "docs-owner4@test.com", "Delta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/auth/me", headers=headers)
    org_id = uuid.UUID(me.json()["org_id"])

    folder = await client.post("/documents/folders", headers=headers, json={"name": "Reports"})
    folder_id = folder.json()["id"]

    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, folder_id=uuid.UUID(folder_id), title="Q1"))

    by_folder = await client.get("/documents", headers=headers, params={"folder_id": folder_id})
    assert by_folder.status_code == 200
    assert [d["title"] for d in by_folder.json()] == ["Q1"]


async def test_tag_missing_document_404(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-owner5@test.com", "Epsilon")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    tag = await client.post("/documents/tags", headers=headers, json={"name": "x"})
    tag_id = tag.json()["id"]

    resp = await client.post(f"/documents/{uuid.uuid4()}/tags/{tag_id}", headers=headers)
    assert resp.status_code == 404


async def test_tenant_isolation_on_folders_and_tags(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "docs-isoa@test.com", "IsoA")
    tokens_b = await _signup(client, "docs-isob@test.com", "IsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    folder = await client.post("/documents/folders", headers=headers_a, json={"name": "Secret"})
    folder_id = folder.json()["id"]
    tag = await client.post("/documents/tags", headers=headers_a, json={"name": "secret-tag"})
    tag_id = tag.json()["id"]

    cross_folder = await client.get(f"/documents/folders/{folder_id}", headers=headers_b)
    assert cross_folder.status_code == 404

    cross_folder_list = await client.get("/documents/folders", headers=headers_b)
    assert cross_folder_list.json() == []

    cross_tag_list = await client.get("/documents/tags", headers=headers_b)
    assert all(t["id"] != tag_id for t in cross_tag_list.json())


async def test_upload_creates_document_uploaded_status(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-up1@test.com", "UpOne")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("handbook.pdf", b"hello world", "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "UPLOADED"
    assert body["checksum"] is not None
    assert body["storage_key"] == f"org/{body['org_id']}/doc/{body['id']}/source.pdf"
    assert body["byte_size"] == len(b"hello world")


async def test_reupload_identical_file_returns_existing_document(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-up2@test.com", "UpTwo")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    file = {"file": ("dup.pdf", b"same bytes", "application/pdf")}

    first = await client.post("/documents/upload", headers=headers, files=file)
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = await client.post("/documents/upload", headers=headers, files=file)
    assert second.status_code == 200
    assert second.json()["id"] == first_id

    listing = await client.get("/documents", headers=headers)
    assert len(listing.json()) == 1


async def test_upload_same_checksum_different_orgs_not_deduped(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "docs-up3@test.com", "UpThreeA")
    tokens_b = await _signup(client, "docs-up4@test.com", "UpThreeB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    file = {"file": ("shared.pdf", b"identical content", "application/pdf")}

    resp_a = await client.post("/documents/upload", headers=headers_a, files=file)
    resp_b = await client.post("/documents/upload", headers=headers_b, files=file)
    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.json()["id"] != resp_b.json()["id"]


async def test_upload_missing_folder_404(client: AsyncClient) -> None:
    tokens = await _signup(client, "docs-up5@test.com", "UpFive")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("x.txt", b"x", "text/plain")},
        data={"folder_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
