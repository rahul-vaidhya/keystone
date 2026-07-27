"""Notebook privacy: a notebook is invisible to every org member except its creator
and explicit ``notebook_shares`` grants — including the org system role owner/admin,
which deliberately does NOT bypass this (unlike every other Access-Role-gated resource
in this app). Shared users get view + chat only, never management rights. The public
embed-widget path (``ctx.user_id is None``) must stay completely unaffected.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.utils.constants import ROLE_ADMIN, ROLE_MEMBER
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


async def _invite(client: AsyncClient, owner_headers: dict, email: str, role: str) -> dict:
    invite = await client.post(
        "/auth/invite", headers=owner_headers, json={"email": email, "role": role}
    )
    assert invite.status_code == 201
    body = invite.json()
    accept = await client.post(
        "/auth/accept-invite",
        json={"org_id": body["org_id"], "token": body["invite_token"], "password": "password123"},
    )
    assert accept.status_code == 200
    return accept.json()


async def _user_id(client: AsyncClient, headers: dict) -> str:
    me = await client.get("/auth/me", headers=headers)
    return me.json()["id"]


async def test_stranger_cannot_see_private_notebook(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner1@test.com", "Acme")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member1@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "Private"})
    notebook_id = created.json()["id"]

    get_resp = await client.get(f"/notebooks/{notebook_id}", headers=member_headers)
    assert get_resp.status_code == 403

    listing = await client.get("/notebooks", headers=member_headers)
    assert listing.json() == []


async def test_org_owner_admin_do_not_bypass_notebook_privacy(client: AsyncClient) -> None:
    """The one place in this app where the system role owner/admin does NOT see
    everything — a member's private notebook stays private even from them."""
    owner_tokens = await _signup(client, "nbs-owner2@test.com", "Beta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    admin_tokens = await _invite(client, owner_headers, "nbs-admin2@test.com", ROLE_ADMIN)
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member2@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    created = await client.post("/notebooks", headers=member_headers, json={"name": "Mine"})
    notebook_id = created.json()["id"]

    owner_get = await client.get(f"/notebooks/{notebook_id}", headers=owner_headers)
    assert owner_get.status_code == 403
    admin_get = await client.get(f"/notebooks/{notebook_id}", headers=admin_headers)
    assert admin_get.status_code == 403

    owner_list = await client.get("/notebooks", headers=owner_headers)
    assert owner_list.json() == []
    admin_list = await client.get("/notebooks", headers=admin_headers)
    assert admin_list.json() == []


async def test_share_grants_view_and_chat_only(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner3@test.com", "Gamma")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member3@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_id = await _user_id(client, member_headers)

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "Shared"})
    notebook_id = created.json()["id"]

    share = await client.post(
        f"/notebooks/{notebook_id}/shares", headers=owner_headers, json={"user_id": member_id}
    )
    assert share.status_code == 204

    # View access works now.
    get_resp = await client.get(f"/notebooks/{notebook_id}", headers=member_headers)
    assert get_resp.status_code == 200
    listing = await client.get("/notebooks", headers=member_headers)
    assert [n["id"] for n in listing.json()] == [notebook_id]

    # Chat/search-level access (retrieval.search -> list_notebook_documents) works too.
    docs = await client.get(f"/notebooks/{notebook_id}/documents", headers=member_headers)
    assert docs.status_code == 200

    # But management stays creator-only.
    rename = await client.patch(
        f"/notebooks/{notebook_id}", headers=member_headers, json={"name": "Renamed"}
    )
    assert rename.status_code == 403
    delete = await client.delete(f"/notebooks/{notebook_id}", headers=member_headers)
    assert delete.status_code == 403
    reshare = await client.post(
        f"/notebooks/{notebook_id}/shares",
        headers=member_headers,
        json={"user_id": member_id},
    )
    assert reshare.status_code == 403


async def test_unshare_revokes_access(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner4@test.com", "Delta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member4@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_id = await _user_id(client, member_headers)

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "Temp share"})
    notebook_id = created.json()["id"]
    await client.post(
        f"/notebooks/{notebook_id}/shares", headers=owner_headers, json={"user_id": member_id}
    )
    assert (
        await client.get(f"/notebooks/{notebook_id}", headers=member_headers)
    ).status_code == 200

    unshare = await client.delete(
        f"/notebooks/{notebook_id}/shares/{member_id}", headers=owner_headers
    )
    assert unshare.status_code == 204

    after = await client.get(f"/notebooks/{notebook_id}", headers=member_headers)
    assert after.status_code == 403


async def test_list_shares_shows_recipient_email(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner5@test.com", "Epsilon")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member5@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_id = await _user_id(client, member_headers)

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "N"})
    notebook_id = created.json()["id"]
    await client.post(
        f"/notebooks/{notebook_id}/shares", headers=owner_headers, json={"user_id": member_id}
    )

    listing = await client.get(f"/notebooks/{notebook_id}/shares", headers=owner_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert len(body) == 1
    assert body[0]["user_id"] == member_id
    assert body[0]["email"] == "nbs-member5@test.com"

    # A non-creator cannot list shares, even when they're one of the share recipients.
    forbidden = await client.get(f"/notebooks/{notebook_id}/shares", headers=member_headers)
    assert forbidden.status_code == 403


async def test_share_with_nonexistent_user_404s(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner6@test.com", "Zeta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "N"})
    notebook_id = created.json()["id"]

    resp = await client.post(
        f"/notebooks/{notebook_id}/shares",
        headers=owner_headers,
        json={"user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_chat_denied_for_stranger_on_private_notebook(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "nbs-owner7@test.com", "Eta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "nbs-member7@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "N"})
    notebook_id = created.json()["id"]

    resp = await client.post(
        "/chat/ask",
        headers=member_headers,
        json={"notebook_id": notebook_id, "query": "anything"},
    )
    assert resp.status_code == 403


async def test_embed_widget_public_path_unaffected_by_privacy(client: AsyncClient) -> None:
    """The anonymous embed-widget path never carries a real user (``ctx.user_id`` stays
    ``None``) and must keep working regardless of notebook privacy."""
    owner_tokens = await _signup(client, "nbs-owner8@test.com", "Theta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = (await client.get("/auth/me", headers=owner_headers)).json()["org_id"]

    created = await client.post("/notebooks", headers=owner_headers, json={"name": "Widgetable"})
    notebook_id = created.json()["id"]

    widget = await client.post(
        "/embed/widgets",
        headers=owner_headers,
        json={"name": "Support Bot", "knowledge_base_id": notebook_id, "allowed_origins": []},
    )
    assert widget.status_code == 201
    public_id = widget.json()["public_id"]

    # No Authorization header at all — the genuine anonymous public path.
    config = await client.get(f"/embed/public/{org_id}/{public_id}/config")
    assert config.status_code == 200
    assert config.json()["notebook_name"] == "Widgetable"
