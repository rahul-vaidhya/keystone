"""Folder-mutation Access-Role gate: a member whose Access Roles don't grant them one
of a folder's effective (inherited) tags can rename/move/delete it, create a subfolder
under it, or move a document into/out of it — reusing the exact tag-inheritance rule
``resolve_allowed_documents`` already applies to document retrieval. Org owner/admin
always bypass this (unlike notebook privacy)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.queue import get_job_queue
from app.services.storage import get_object_store
from app.utils.constants import ROLE_ADMIN, ROLE_MEMBER
from main import app
from tests.conftest import FakeJobQueue


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
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_job_queue, None)


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


async def _mkfolder(
    client: AsyncClient, headers: dict, name: str, parent_id: str | None = None
) -> dict:
    body = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    resp = await client.post("/documents/folders", headers=headers, json=body)
    assert resp.status_code == 201
    return resp.json()


async def _upload(
    client: AsyncClient, headers: dict, name: str, folder_id: str | None = None
) -> dict:
    data = {"folder_id": folder_id} if folder_id else {}
    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": (name, name.encode(), "application/pdf")},
        data=data,
    )
    assert resp.status_code == 201
    return resp.json()


async def _restrict_folder(
    client: AsyncClient, owner_headers: dict, folder_id: str
) -> tuple[str, str]:
    """Creates a tag, tags the folder with it, grants the tag to a new Access Role, and
    returns (role_id, tag_id) — the folder is now access-controlling but nobody is
    assigned to the role yet."""
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "Restricted"})
    tag_id = tag.json()["id"]
    tagged = await client.post(
        f"/documents/folders/{folder_id}/tags/{tag_id}", headers=owner_headers
    )
    assert tagged.status_code == 204
    role = await client.post("/access-roles", headers=owner_headers, json={"name": "Vault"})
    role_id = role.json()["id"]
    grant = await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=owner_headers)
    assert grant.status_code == 204
    return role_id, tag_id


async def test_ungranted_member_cannot_rename_move_delete_restricted_folder(
    client: AsyncClient,
) -> None:
    owner_tokens = await _signup(client, "fag-owner1@test.com", "Acme")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "fag-member1@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    folder = await _mkfolder(client, owner_headers, "Vault")
    other = await _mkfolder(client, owner_headers, "Open")
    await _restrict_folder(client, owner_headers, folder["id"])

    rename = await client.patch(
        f"/documents/folders/{folder['id']}", headers=member_headers, json={"name": "Renamed"}
    )
    assert rename.status_code == 403

    move = await client.post(
        f"/documents/folders/{folder['id']}/move",
        headers=member_headers,
        json={"parent_id": None},
    )
    assert move.status_code == 403

    move_into = await client.post(
        f"/documents/folders/{other['id']}/move",
        headers=member_headers,
        json={"parent_id": folder["id"]},
    )
    assert move_into.status_code == 403

    delete = await client.delete(f"/documents/folders/{folder['id']}", headers=member_headers)
    assert delete.status_code == 403

    subfolder = await client.post(
        "/documents/folders",
        headers=member_headers,
        json={"name": "Child", "parent_id": folder["id"]},
    )
    assert subfolder.status_code == 403


async def test_granted_member_can_manage_restricted_folder(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "fag-owner2@test.com", "Beta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "fag-member2@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_id = await _user_id(client, member_headers)

    folder = await _mkfolder(client, owner_headers, "Vault")
    role_id, _tag_id = await _restrict_folder(client, owner_headers, folder["id"])
    assign = await client.post(f"/access-roles/{role_id}/users/{member_id}", headers=owner_headers)
    assert assign.status_code == 204

    rename = await client.patch(
        f"/documents/folders/{folder['id']}", headers=member_headers, json={"name": "Renamed"}
    )
    assert rename.status_code == 200

    subfolder = await client.post(
        "/documents/folders",
        headers=member_headers,
        json={"name": "Child", "parent_id": folder["id"]},
    )
    assert subfolder.status_code == 201

    delete = await client.delete(
        f"/documents/folders/{subfolder.json()['id']}", headers=member_headers
    )
    assert delete.status_code == 204


async def test_owner_and_admin_always_bypass_folder_gate(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "fag-owner3@test.com", "Gamma")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    admin_tokens = await _invite(client, owner_headers, "fag-admin3@test.com", ROLE_ADMIN)
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    folder = await _mkfolder(client, owner_headers, "Vault")
    await _restrict_folder(client, owner_headers, folder["id"])

    # Owner (who created and restricted it) can still manage it.
    owner_rename = await client.patch(
        f"/documents/folders/{folder['id']}", headers=owner_headers, json={"name": "Still owner"}
    )
    assert owner_rename.status_code == 200

    # Admin, who was never granted the role, still bypasses.
    admin_rename = await client.patch(
        f"/documents/folders/{folder['id']}", headers=admin_headers, json={"name": "Admin too"}
    )
    assert admin_rename.status_code == 200


async def test_move_document_into_and_out_of_restricted_folder_gated(
    client: AsyncClient,
) -> None:
    owner_tokens = await _signup(client, "fag-owner4@test.com", "Delta")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "fag-member4@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    restricted = await _mkfolder(client, owner_headers, "Vault")
    await _restrict_folder(client, owner_headers, restricted["id"])
    doc_in_restricted = await _upload(client, owner_headers, "a.pdf", restricted["id"])
    doc_at_root = await _upload(client, owner_headers, "b.pdf")

    # Member cannot move an unrestricted document INTO the restricted folder.
    move_in = await client.patch(
        f"/documents/{doc_at_root['id']}/folder",
        headers=member_headers,
        json={"folder_id": restricted["id"]},
    )
    assert move_in.status_code == 403

    # Member cannot move the document already inside it back OUT, either.
    move_out = await client.patch(
        f"/documents/{doc_in_restricted['id']}/folder",
        headers=member_headers,
        json={"folder_id": None},
    )
    assert move_out.status_code == 403

    # Owner (creator, always allowed) can do both.
    owner_move = await client.patch(
        f"/documents/{doc_in_restricted['id']}/folder",
        headers=owner_headers,
        json={"folder_id": None},
    )
    assert owner_move.status_code == 200


async def test_folder_still_visible_when_browsing_despite_being_restricted(
    client: AsyncClient,
) -> None:
    """Browsing stays open (this app's established convention) — only the mutating
    actions are gated. can_manage reflects the gate without hiding the folder."""
    owner_tokens = await _signup(client, "fag-owner5@test.com", "Epsilon")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "fag-member5@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    folder = await _mkfolder(client, owner_headers, "Vault")
    await _restrict_folder(client, owner_headers, folder["id"])

    listing = await client.get("/documents/folders", headers=member_headers)
    assert listing.status_code == 200
    by_id = {f["id"]: f for f in listing.json()}
    assert folder["id"] in by_id
    assert by_id[folder["id"]]["can_manage"] is False

    owner_listing = await client.get("/documents/folders", headers=owner_headers)
    owner_by_id = {f["id"]: f for f in owner_listing.json()}
    assert owner_by_id[folder["id"]]["can_manage"] is True
