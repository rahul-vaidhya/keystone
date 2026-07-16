"""Access Role CRUD, grant/revoke tag, assign/remove member, admin gating, name
conflict, and tenant isolation (docs/access-roles-dnd-plan.md). Tag-based
``resolve_allowed_documents`` behavior itself is covered in test_retrieval.py.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.utils.constants import ROLE_MEMBER
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


async def _invite_member(client: AsyncClient, owner_headers: dict, email: str) -> dict:
    """Invites a member and accepts the invite (self-serve link flow — the invitee sets
    their own password), returning their own token dict."""
    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": email, "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201
    body = invite.json()
    accept = await client.post(
        "/auth/accept-invite",
        json={"org_id": body["org_id"], "token": body["invite_token"], "password": "password123"},
    )
    assert accept.status_code == 200
    return accept.json()


async def test_create_and_list_access_role(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner1@test.com", "Acme")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    created = await client.post("/access-roles", headers=headers, json={"name": "Finance Team"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Finance Team"
    assert body["tag_ids"] == []
    assert body["user_ids"] == []

    listing = await client.get("/access-roles", headers=headers)
    assert listing.status_code == 200
    assert [r["name"] for r in listing.json()] == ["Finance Team"]


async def test_create_access_role_requires_admin(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "ar-owner2@test.com", "Beta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite_member(client, owner_headers, "ar-member2@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    resp = await client.post("/access-roles", headers=member_headers, json={"name": "X"})
    assert resp.status_code == 403


async def test_duplicate_name_conflict(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner3@test.com", "Gamma")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    first = await client.post("/access-roles", headers=headers, json={"name": "Legal"})
    assert first.status_code == 201
    dup = await client.post("/access-roles", headers=headers, json={"name": "Legal"})
    assert dup.status_code == 409


async def test_delete_access_role(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner4@test.com", "Delta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    created = await client.post("/access-roles", headers=headers, json={"name": "Temp"})
    role_id = created.json()["id"]

    resp = await client.delete(f"/access-roles/{role_id}", headers=headers)
    assert resp.status_code == 204

    listing = await client.get("/access-roles", headers=headers)
    assert listing.json() == []

    missing = await client.delete(f"/access-roles/{role_id}", headers=headers)
    assert missing.status_code == 404


async def test_grant_and_revoke_tag(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner5@test.com", "Epsilon")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    role = await client.post("/access-roles", headers=headers, json={"name": "R"})
    role_id = role.json()["id"]
    tag = await client.post("/documents/tags", headers=headers, json={"name": "Finance"})
    tag_id = tag.json()["id"]

    grant = await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=headers)
    assert grant.status_code == 204

    listing = await client.get("/access-roles", headers=headers)
    assert listing.json()[0]["tag_ids"] == [tag_id]

    revoke = await client.delete(f"/access-roles/{role_id}/tags/{tag_id}", headers=headers)
    assert revoke.status_code == 204

    listing_after = await client.get("/access-roles", headers=headers)
    assert listing_after.json()[0]["tag_ids"] == []


async def test_grant_nonexistent_tag_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner6@test.com", "Zeta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    role = await client.post("/access-roles", headers=headers, json={"name": "R"})
    role_id = role.json()["id"]

    resp = await client.post(f"/access-roles/{role_id}/tags/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_assign_and_remove_user(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "ar-owner7@test.com", "Eta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite_member(client, owner_headers, "ar-member7@test.com")
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {member_tokens['access_token']}"}
    )
    member_id = me.json()["id"]

    role = await client.post("/access-roles", headers=owner_headers, json={"name": "R"})
    role_id = role.json()["id"]

    assign = await client.post(f"/access-roles/{role_id}/users/{member_id}", headers=owner_headers)
    assert assign.status_code == 204

    listing = await client.get("/access-roles", headers=owner_headers)
    assert listing.json()[0]["user_ids"] == [member_id]

    remove = await client.delete(
        f"/access-roles/{role_id}/users/{member_id}", headers=owner_headers
    )
    assert remove.status_code == 204

    listing_after = await client.get("/access-roles", headers=owner_headers)
    assert listing_after.json()[0]["user_ids"] == []


async def test_assign_nonexistent_user_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "ar-owner8@test.com", "Theta")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    role = await client.post("/access-roles", headers=headers, json={"name": "R"})
    role_id = role.json()["id"]

    resp = await client.post(f"/access-roles/{role_id}/users/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_grant_tag_requires_admin(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "ar-owner9@test.com", "Iota")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite_member(client, owner_headers, "ar-member9@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    role = await client.post("/access-roles", headers=owner_headers, json={"name": "R"})
    role_id = role.json()["id"]
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "T"})
    tag_id = tag.json()["id"]

    resp = await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=member_headers)
    assert resp.status_code == 403


async def test_tenant_isolation_on_access_roles(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "ar-isoa@test.com", "IsoA")
    tokens_b = await _signup(client, "ar-isob@test.com", "IsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    role = await client.post("/access-roles", headers=headers_a, json={"name": "Secret"})
    role_id = role.json()["id"]

    cross_list = await client.get("/access-roles", headers=headers_b)
    assert cross_list.json() == []

    cross_delete = await client.delete(f"/access-roles/{role_id}", headers=headers_b)
    assert cross_delete.status_code == 404

    still_there = await client.get("/access-roles", headers=headers_a)
    assert len(still_there.json()) == 1
