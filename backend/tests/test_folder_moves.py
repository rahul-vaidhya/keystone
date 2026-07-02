"""F25 folder move/rename/delete: cycle/collision correctness, the same-transaction
subtree path rebuild (never by slicing the old path string), the three delete modes, and
org isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.documents import Folder, FolderCreate
from app.services.documents import FolderNameConflict, FolderNotFound, documents_service
from app.services.documents.folders import (
    FolderRepository,
    create_folder,
    move_folder,
    rename_folder,
)
from app.services.queue import get_job_queue
from app.services.storage import get_object_store
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


# --- rename ---------------------------------------------------------------------------


async def test_rename_folder_updates_name_and_path(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-rename1@test.com", "Org1")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    folder = await _mkfolder(client, headers, "HR")

    resp = await client.patch(
        f"/documents/folders/{folder['id']}", headers=headers, json={"name": "Personnel"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Personnel"
    assert body["path"] == "Personnel"


async def test_rename_no_false_collision_with_self(client: AsyncClient) -> None:
    """Re-saving the same name must not 409 against itself."""
    tokens = await _signup(client, "fm-rename2@test.com", "Org2")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    folder = await _mkfolder(client, headers, "HR")

    resp = await client.patch(
        f"/documents/folders/{folder['id']}", headers=headers, json={"name": "HR"}
    )
    assert resp.status_code == 200


# --- multi-generation path rebuild, the core correctness requirement ------------------


async def test_path_rebuild_multi_generation_with_sibling_name_prefix_collision_risk(
    client: AsyncClient,
) -> None:
    """The strong regression test: a 3-level subtree under the renamed folder, PLUS a
    sibling folder ("HR-Archive") sharing a name prefix with the renamed folder ("HR").
    A buggy implementation that found "descendants" via string-prefix matching on the OLD
    path (e.g. ``path LIKE 'HR%'``) or rebuilt children via ``descendant.path[len(old):]``
    slicing would risk corrupting "HR-Archive" (false-prefix-matched) or mis-deriving a
    descendant's suffix. The correct implementation finds descendants via parent_id only
    and derives every path from parent_id + name, so "HR-Archive" must be completely
    untouched and the 3-level subtree must be exactly and correctly rebuilt.
    """
    tokens = await _signup(client, "fm-pathrebuild@test.com", "Org3")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    hr = await _mkfolder(client, headers, "HR")
    hr_archive = await _mkfolder(client, headers, "HR-Archive")  # sibling, shares name prefix
    policies = await _mkfolder(client, headers, "Policies", hr["id"])
    leave = await _mkfolder(client, headers, "Leave", policies["id"])  # grandchild
    doc = await _upload(client, headers, "policy.pdf", leave["id"])

    resp = await client.patch(
        f"/documents/folders/{hr['id']}", headers=headers, json={"name": "Personnel"}
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "Personnel"

    listing = (await client.get("/documents/folders", headers=headers)).json()
    by_id = {f["id"]: f for f in listing}

    assert by_id[hr["id"]]["path"] == "Personnel"
    assert by_id[policies["id"]]["path"] == "Personnel/Policies"
    assert by_id[leave["id"]]["path"] == "Personnel/Policies/Leave"
    # the sibling must be entirely untouched
    assert by_id[hr_archive["id"]]["path"] == "HR-Archive"
    assert by_id[hr_archive["id"]]["name"] == "HR-Archive"

    doc_listing = (await client.get("/documents", headers=headers)).json()
    assert next(d for d in doc_listing if d["id"] == doc["id"])["folder_id"] == leave["id"]


async def test_move_folder_rebuilds_descendant_paths(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-move1@test.com", "Org4")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    sales = await _mkfolder(client, headers, "Sales")
    hr = await _mkfolder(client, headers, "HR")
    policies = await _mkfolder(client, headers, "Policies", hr["id"])

    resp = await client.post(
        f"/documents/folders/{hr['id']}/move", headers=headers, json={"parent_id": sales["id"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_id"] == sales["id"]
    assert body["path"] == "Sales/HR"

    listing = (await client.get("/documents/folders", headers=headers)).json()
    by_id = {f["id"]: f for f in listing}
    assert by_id[policies["id"]]["path"] == "Sales/HR/Policies"


async def test_move_folder_to_root_sets_bare_path(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-move2@test.com", "Org5")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    parent = await _mkfolder(client, headers, "Parent")
    child = await _mkfolder(client, headers, "Child", parent["id"])

    resp = await client.post(
        f"/documents/folders/{child['id']}/move", headers=headers, json={"parent_id": None}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_id"] is None
    assert body["path"] == "Child"


# --- cycle rejection -------------------------------------------------------------------


async def test_move_into_self_rejected(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-cycle1@test.com", "Org6")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    folder = await _mkfolder(client, headers, "A")

    resp = await client.post(
        f"/documents/folders/{folder['id']}/move", headers=headers, json={"parent_id": folder["id"]}
    )
    assert resp.status_code == 400


async def test_move_into_direct_child_rejected(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-cycle2@test.com", "Org7")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    parent = await _mkfolder(client, headers, "A")
    child = await _mkfolder(client, headers, "B", parent["id"])

    resp = await client.post(
        f"/documents/folders/{parent['id']}/move", headers=headers, json={"parent_id": child["id"]}
    )
    assert resp.status_code == 400


async def test_move_into_deep_descendant_rejected(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-cycle3@test.com", "Org8")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    a = await _mkfolder(client, headers, "A")
    b = await _mkfolder(client, headers, "B", a["id"])
    c = await _mkfolder(client, headers, "C", b["id"])  # 3 levels deep

    resp = await client.post(
        f"/documents/folders/{a['id']}/move", headers=headers, json={"parent_id": c["id"]}
    )
    assert resp.status_code == 400


# --- name-collision rejection -----------------------------------------------------------


async def test_rename_collision_with_sibling(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-collide1@test.com", "Org9")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await _mkfolder(client, headers, "HR")
    other = await _mkfolder(client, headers, "Sales")

    resp = await client.patch(
        f"/documents/folders/{other['id']}", headers=headers, json={"name": "HR"}
    )
    assert resp.status_code == 409


async def test_move_collision_at_target_parent(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-collide2@test.com", "Org10")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    target = await _mkfolder(client, headers, "Target")
    await _mkfolder(client, headers, "Conflict", target["id"])
    mover = await _mkfolder(client, headers, "Conflict")  # same name, currently at root

    resp = await client.post(
        f"/documents/folders/{mover['id']}/move", headers=headers, json={"parent_id": target["id"]}
    )
    assert resp.status_code == 409


# --- create: duplicate-name 409s, root and sibling alike (pre-existing gap, fixed here) -

# Pre-existing gap, separate from and more severe than the root-uniqueness finding below:
# create_folder previously had neither an application-level collision check nor an
# IntegrityError->409 translation at all, so ANY duplicate-name create (root or sibling,
# no race required) raised an unhandled IntegrityError -> 500. The two tests below cover
# the everyday (non-forced) path for both cases; the forced-race test further down proves
# the constraint-level backstop for the root case specifically.


async def test_create_duplicate_root_folder_name_is_409_not_500(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-rootdup@test.com", "OrgRootDup")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await _mkfolder(client, headers, "Shared")

    resp = await client.post("/documents/folders", headers=headers, json={"name": "Shared"})
    assert resp.status_code == 409


async def test_create_duplicate_sibling_folder_name_is_409_not_500(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-siblingdup@test.com", "OrgSiblingDup")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    parent = await _mkfolder(client, headers, "Parent")
    await _mkfolder(client, headers, "Child", parent["id"])

    resp = await client.post(
        "/documents/folders", headers=headers, json={"name": "Child", "parent_id": parent["id"]}
    )
    assert resp.status_code == 409


# --- concurrent-move backstop: constraint violation -> 409, not a raw 500 --------------


async def test_concurrent_rename_constraint_violation_translated_to_409(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Simulates the residual race the plan calls out: the application-level collision
    check passes (forced via monkeypatch, modeling two concurrent transactions each
    seeing a pre-collision state), but a colliding row already exists by flush time. The
    unique constraint (``uq_folders_org_parent_name``) is the actual backstop — this
    proves the IntegrityError it raises is translated to FolderNameConflict, never a raw
    500, exactly as F12's checksum-dedupe race was handled."""
    # Uses a real (non-null) parent_id: exercises uq_folders_org_parent_name itself. The
    # sibling test below (test_concurrent_root_create_constraint_violation_translated_to_409)
    # exercises the partial unique index that now closes the root-level (parent_id IS NULL)
    # gap this constraint alone never covered — see that test for detail.
    org_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="RaceOrg"))
        await session.flush()
        parent = Folder(org_id=org_id, parent_id=None, name="Parent", path="Parent")
        session.add(parent)
        await session.flush()
        existing = Folder(org_id=org_id, parent_id=parent.id, name="Taken", path="Parent/Taken")
        mover = Folder(org_id=org_id, parent_id=parent.id, name="Mover", path="Parent/Mover")
        session.add_all([existing, mover])
        await session.flush()
        mover_id = mover.id

    async def _fake_no_conflict(self, *args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(FolderRepository, "exists_name_conflict", _fake_no_conflict)

    ctx = TenantContext(org_id=org_id)
    with pytest.raises(FolderNameConflict):
        await rename_folder(ctx, mover_id, "Taken")


async def test_concurrent_root_create_constraint_violation_translated_to_409(
    session_factory, tenant_engine, monkeypatch
) -> None:
    """Closes the F25-flagged root-level gap: ``uq_folders_org_parent_name`` gives no
    backstop between two ROOT-level folders sharing a name (Postgres treats
    ``NULL != NULL`` for uniqueness), so a genuine concurrent race at root level had no
    constraint to catch it — only the (non-concurrency-safe) application-level check did.
    The new partial unique index (``uq_folders_org_root_name``, migration 0010, scoped to
    ``parent_id IS NULL``) is the actual backstop now. Mirrors
    ``test_concurrent_rename_constraint_violation_translated_to_409``: the application
    check is forced to a false negative via monkeypatch (modeling two concurrent creates
    each seeing a pre-collision state); the index is what turns the resulting
    IntegrityError into a 409 instead of a raw 500."""
    org_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="RootRaceOrg"))
        await session.flush()
        session.add(Folder(org_id=org_id, parent_id=None, name="Taken", path="Taken"))
        await session.flush()

    async def _fake_no_conflict(self, *args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(FolderRepository, "exists_name_conflict", _fake_no_conflict)

    ctx = TenantContext(org_id=org_id)
    with pytest.raises(FolderNameConflict):
        await create_folder(ctx, FolderCreate(name="Taken"))


# --- delete: block (default), cascade, reflow -------------------------------------------


async def test_block_delete_rejects_folder_with_child_folder(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-del1@test.com", "Org11")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    parent = await _mkfolder(client, headers, "Parent")
    await _mkfolder(client, headers, "Child", parent["id"])

    resp = await client.delete(f"/documents/folders/{parent['id']}", headers=headers)
    assert resp.status_code == 409


async def test_block_delete_rejects_folder_with_document(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-del2@test.com", "Org12")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    folder = await _mkfolder(client, headers, "Docs")
    await _upload(client, headers, "a.pdf", folder["id"])

    resp = await client.delete(f"/documents/folders/{folder['id']}", headers=headers)
    assert resp.status_code == 409


async def test_cascade_delete_multi_level_subtree_documents_survive_orphaned(
    client: AsyncClient,
) -> None:
    """Multi-level subtree (root -> child -> grandchild) with documents at EVERY depth.
    Cascade-deleting the root must remove all 3 folder rows but every document must
    survive with folder_id=None — never deleted (architecture.md: folder is not a
    permission boundary)."""
    tokens = await _signup(client, "fm-del3@test.com", "Org13")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    root = await _mkfolder(client, headers, "Root")
    mid = await _mkfolder(client, headers, "Mid", root["id"])
    leaf = await _mkfolder(client, headers, "Leaf", mid["id"])

    doc_root = await _upload(client, headers, "root.pdf", root["id"])
    doc_mid = await _upload(client, headers, "mid.pdf", mid["id"])
    doc_leaf = await _upload(client, headers, "leaf.pdf", leaf["id"])

    resp = await client.delete(
        f"/documents/folders/{root['id']}", headers=headers, params={"mode": "cascade"}
    )
    assert resp.status_code == 204

    for fid in (root["id"], mid["id"], leaf["id"]):
        assert (await client.get(f"/documents/folders/{fid}", headers=headers)).status_code == 404

    doc_listing = {d["id"]: d for d in (await client.get("/documents", headers=headers)).json()}
    for doc in (doc_root, doc_mid, doc_leaf):
        assert doc_listing[doc["id"]]["folder_id"] is None


async def test_reflow_delete_moves_children_and_documents_to_parent(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-del4@test.com", "Org14")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    grandparent = await _mkfolder(client, headers, "Grandparent")
    middle = await _mkfolder(client, headers, "Middle", grandparent["id"])
    child = await _mkfolder(client, headers, "Child", middle["id"])
    doc = await _upload(client, headers, "direct.pdf", middle["id"])

    resp = await client.delete(
        f"/documents/folders/{middle['id']}", headers=headers, params={"mode": "reflow"}
    )
    assert resp.status_code == 204

    listing = {f["id"]: f for f in (await client.get("/documents/folders", headers=headers)).json()}
    assert middle["id"] not in listing
    assert listing[child["id"]]["parent_id"] == grandparent["id"]
    assert listing[child["id"]]["path"] == "Grandparent/Child"

    doc_listing = {d["id"]: d for d in (await client.get("/documents", headers=headers)).json()}
    assert doc_listing[doc["id"]]["folder_id"] == grandparent["id"]


async def test_reflow_delete_at_root_moves_children_to_root(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-del5@test.com", "Org15")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    root_folder = await _mkfolder(client, headers, "Root")
    child = await _mkfolder(client, headers, "Child", root_folder["id"])

    resp = await client.delete(
        f"/documents/folders/{root_folder['id']}", headers=headers, params={"mode": "reflow"}
    )
    assert resp.status_code == 204

    listing = {f["id"]: f for f in (await client.get("/documents/folders", headers=headers)).json()}
    assert listing[child["id"]]["parent_id"] is None
    assert listing[child["id"]]["path"] == "Child"


async def test_reflow_delete_collision_at_target_409(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-del6@test.com", "Org16")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    parent = await _mkfolder(client, headers, "Parent")
    # sibling of the deleted folder, post-reflow
    await _mkfolder(client, headers, "Dup", parent["id"])
    to_delete = await _mkfolder(client, headers, "ToDelete", parent["id"])
    # would collide with the existing "Dup" once reflowed up
    await _mkfolder(client, headers, "Dup", to_delete["id"])

    resp = await client.delete(
        f"/documents/folders/{to_delete['id']}", headers=headers, params={"mode": "reflow"}
    )
    assert resp.status_code == 409


# --- org isolation -----------------------------------------------------------------------


async def test_rename_move_delete_cross_org_404(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "fm-isoa@test.com", "IsoFmA")
    tokens_b = await _signup(client, "fm-isob@test.com", "IsoFmB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    folder_b = await _mkfolder(client, headers_b, "BFolder")

    rename_resp = await client.patch(
        f"/documents/folders/{folder_b['id']}", headers=headers_a, json={"name": "Hijacked"}
    )
    assert rename_resp.status_code == 404

    move_resp = await client.post(
        f"/documents/folders/{folder_b['id']}/move", headers=headers_a, json={"parent_id": None}
    )
    assert move_resp.status_code == 404

    delete_resp = await client.delete(f"/documents/folders/{folder_b['id']}", headers=headers_a)
    assert delete_resp.status_code == 404


async def test_move_target_parent_in_other_org_404(session_factory, tenant_engine) -> None:
    """Repository-level independent backstop: move_folder is called directly with org A's
    ctx but org B's folder id as the target parent — must 404, not silently attach across
    tenants. Mirrors F31's search_chunks org-scoping backstop test shape."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="MoveOrgA"))
        session.add(Organization(id=org_b, name="MoveOrgB"))
        await session.flush()
        mover = Folder(org_id=org_a, parent_id=None, name="Mover", path="Mover")
        other_org_folder = Folder(org_id=org_b, parent_id=None, name="Other", path="Other")
        session.add_all([mover, other_org_folder])
        await session.flush()
        mover_id, other_org_folder_id = mover.id, other_org_folder.id

    ctx_a = TenantContext(org_id=org_a)
    with pytest.raises(FolderNotFound):
        await move_folder(ctx_a, mover_id, other_org_folder_id)


# sanity: documents_service delegation wired correctly end to end (not just the free fns)
async def test_documents_service_delegates_rename_move_delete(client: AsyncClient) -> None:
    tokens = await _signup(client, "fm-delegate@test.com", "OrgDeleg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/auth/me", headers=headers)
    org_id = uuid.UUID(me.json()["org_id"])
    ctx = TenantContext(org_id=org_id)

    folder = await _mkfolder(client, headers, "Svc")
    renamed = await documents_service.rename_folder(ctx, uuid.UUID(folder["id"]), "SvcRenamed")
    assert renamed.name == "SvcRenamed"
    moved = await documents_service.move_folder(ctx, uuid.UUID(folder["id"]), None)
    assert moved.parent_id is None
    await documents_service.delete_folder(ctx, uuid.UUID(folder["id"]))
    final = await client.get(f"/documents/folders/{folder['id']}", headers=headers)
    assert final.status_code == 404
