"""F30 notebooks: CRUD, document association, idempotency, tenant isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.documents.models import Document
from main import app


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


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


async def test_create_notebook(client: AsyncClient) -> None:
    tokens = await _signup(client, "kb-owner1@test.com", "Acme")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    org_id = await _org_id(client, headers)
    resp = await client.post(
        "/notebooks", headers=headers, json={"name": "Onboarding", "description": "New hires"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Onboarding"
    assert body["description"] == "New hires"
    assert body["org_id"] == str(org_id)


async def test_update_notebook_metadata(client: AsyncClient) -> None:
    tokens = await _signup(client, "kb-owner2@test.com", "Beta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    created = await client.post("/notebooks", headers=headers, json={"name": "Draft"})
    notebook_id = created.json()["id"]

    updated = await client.patch(
        f"/notebooks/{notebook_id}",
        headers=headers,
        json={"name": "Final", "description": "Renamed"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "Final"
    assert body["description"] == "Renamed"


async def test_delete_notebook(client: AsyncClient) -> None:
    tokens = await _signup(client, "kb-owner3@test.com", "Gamma")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    created = await client.post("/notebooks", headers=headers, json={"name": "Temp"})
    notebook_id = created.json()["id"]

    resp = await client.delete(f"/notebooks/{notebook_id}", headers=headers)
    assert resp.status_code == 204

    missing = await client.get(f"/notebooks/{notebook_id}", headers=headers)
    assert missing.status_code == 404


async def test_list_notebooks(client: AsyncClient) -> None:
    tokens = await _signup(client, "kb-owner4@test.com", "Delta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    await client.post("/notebooks", headers=headers, json={"name": "One"})
    await client.post("/notebooks", headers=headers, json={"name": "Two"})

    listing = await client.get("/notebooks", headers=headers)
    assert listing.status_code == 200
    names = {n["name"] for n in listing.json()}
    assert names == {"One", "Two"}


async def test_attach_and_list_documents(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "kb-owner5@test.com", "Epsilon")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    created = await client.post("/notebooks", headers=headers, json={"name": "Research"})
    notebook_id = created.json()["id"]
    doc_id = await _seed_document(session_factory, org_id, "Handbook")

    attach = await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    assert attach.status_code == 204

    listing = await client.get(f"/notebooks/{notebook_id}/documents", headers=headers)
    assert listing.status_code == 200
    assert [d["title"] for d in listing.json()] == ["Handbook"]


async def test_attach_is_idempotent(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "kb-owner6@test.com", "Zeta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "Notes"})).json()[
        "id"
    ]
    doc_id = await _seed_document(session_factory, org_id, "Policy")

    first = await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    second = await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204

    listing = await client.get(f"/notebooks/{notebook_id}/documents", headers=headers)
    assert [d["id"] for d in listing.json()] == [str(doc_id)]


async def test_detach_is_idempotent(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "kb-owner7@test.com", "Eta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "Notes"})).json()[
        "id"
    ]
    doc_id = await _seed_document(session_factory, org_id, "Policy")
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    first = await client.delete(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    second = await client.delete(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204

    listing = await client.get(f"/notebooks/{notebook_id}/documents", headers=headers)
    assert listing.json() == []


async def test_document_in_two_notebooks_shares_one_row(
    client: AsyncClient, session_factory
) -> None:
    """F30 DoD: a document in two notebooks has exactly one set of chunks/embeddings — i.e.
    attaching to a second notebook never duplicates the document row; both notebooks list the
    SAME document id."""
    tokens = await _signup(client, "kb-owner8@test.com", "Theta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    nb_a = (await client.post("/notebooks", headers=headers, json={"name": "A"})).json()["id"]
    nb_b = (await client.post("/notebooks", headers=headers, json={"name": "B"})).json()["id"]
    doc_id = await _seed_document(session_factory, org_id, "Shared")

    await client.post(f"/notebooks/{nb_a}/documents/{doc_id}", headers=headers)
    await client.post(f"/notebooks/{nb_b}/documents/{doc_id}", headers=headers)

    list_a = await client.get(f"/notebooks/{nb_a}/documents", headers=headers)
    list_b = await client.get(f"/notebooks/{nb_b}/documents", headers=headers)
    assert [d["id"] for d in list_a.json()] == [str(doc_id)]
    assert [d["id"] for d in list_b.json()] == [str(doc_id)]


async def test_attach_missing_document_404(client: AsyncClient) -> None:
    tokens = await _signup(client, "kb-owner9@test.com", "Iota")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "Empty"})).json()[
        "id"
    ]

    resp = await client.post(f"/notebooks/{notebook_id}/documents/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_attach_to_missing_notebook_404(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "kb-owner10@test.com", "Kappa")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_id = await _seed_document(session_factory, org_id, "Orphan target")

    resp = await client.post(f"/notebooks/{uuid.uuid4()}/documents/{doc_id}", headers=headers)
    assert resp.status_code == 404


async def test_tenant_isolation_on_notebooks(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "kb-isoa@test.com", "IsoA")
    tokens_b = await _signup(client, "kb-isob@test.com", "IsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    org_b = await _org_id(client, headers_b)

    notebook = await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    notebook_id = notebook.json()["id"]

    cross_get = await client.get(f"/notebooks/{notebook_id}", headers=headers_b)
    assert cross_get.status_code == 404

    cross_list = await client.get("/notebooks", headers=headers_b)
    assert cross_list.json() == []

    cross_delete = await client.delete(f"/notebooks/{notebook_id}", headers=headers_b)
    assert cross_delete.status_code == 404

    # org B's document cannot be attached to org A's notebook (cross-org attach denied).
    org_b_doc = await _seed_document(session_factory, org_b, "B's doc")
    cross_attach = await client.post(
        f"/notebooks/{notebook_id}/documents/{org_b_doc}", headers=headers_a
    )
    assert cross_attach.status_code == 404
