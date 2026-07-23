"""Embed widget: admin CRUD (owner/admin-only, cross-org isolation), the public config
endpoint (anti-enumeration 404s), the public chat-stream endpoint (origin allowlist,
rate limiting, widget-originated persistence), and the anonymous-ctx access-controlling
tag-gating behavior (`resolve_user_granted_tags(user_id=None)` — previously unexercised).

Uses the FAKE embedder/LLM seams exclusively (default settings) and an in-memory
`FakeRateLimiter` override — deterministic, no network, no real Redis.
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.middleware.context import TenantContext
from app.models.chat import Conversation
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding
from app.services.retrieval import resolve_allowed_documents
from app.services.seams import EMBED_DIM
from app.utils.constants import ROLE_MEMBER
from app.utils.rate_limit import get_rate_limiter
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


class FakeRateLimiter:
    """In-memory test double for `app.utils.rate_limit.RateLimiter` — records every hit
    and returns a fixed allow/deny decision, so the offline suite never touches Redis."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[str] = []

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        self.calls.append(key)
        return self.allow


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_rate_limiter, None)


async def _signup(client: AsyncClient, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org_name}
    )
    assert resp.status_code == 201
    return resp.json()


async def _org_id(client: AsyncClient, headers: dict) -> uuid.UUID:
    me = await client.get("/auth/me", headers=headers)
    return uuid.UUID(me.json()["org_id"])


async def _invite_member(client: AsyncClient, owner_headers: dict, email: str) -> dict:
    invite = await client.post(
        "/auth/invite", headers=owner_headers, json={"email": email, "role": ROLE_MEMBER}
    )
    assert invite.status_code == 201
    body = invite.json()
    accept = await client.post(
        "/auth/accept-invite",
        json={"org_id": body["org_id"], "token": body["invite_token"], "password": "password123"},
    )
    assert accept.status_code == 200
    return accept.json()


def _vector(seed: int, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    vec[seed % dim] = 1.0
    return vec


async def _seed_document(session_factory, org_id: uuid.UUID, title: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title))
    return doc_id


async def _seed_chunk_with_embedding(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    ordinal: int,
    content: str,
    seed: int = 0,
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                section_id=None,
                ordinal=ordinal,
                content=content,
                token_count=max(len(content) // 4, 1),
                char_start=0,
                char_end=len(content),
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=document_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model=FAKE_MODEL,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return chunk_id


def _parse_sse_events(response_text: str) -> list[dict]:
    events = []
    for block in response_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("data: "):
            events.append(json.loads(block[6:]))
    return events


# ---- Admin CRUD ----


async def test_create_widget_returns_public_id_snippet_and_iframe_url(
    client: AsyncClient,
) -> None:
    tokens = await _signup(client, "embed-crud@test.com", "CrudOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]

    resp = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "Site widget", "allowed_origins": []},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Site widget"
    assert body["knowledge_base_id"] == notebook_id
    assert body["public_id"]
    assert body["is_active"] is True
    assert body["allowed_origins"] == []
    assert str(org_id) in body["iframe_url"]
    assert body["public_id"] in body["iframe_url"]
    assert body["public_id"] in body["embed_snippet"]
    assert "<script" in body["embed_snippet"]


async def test_list_widgets_returns_created_widgets(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-list@test.com", "ListOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    await client.post(
        "/embed/widgets", headers=headers, json={"knowledge_base_id": notebook_id, "name": "W1"}
    )
    await client.post(
        "/embed/widgets", headers=headers, json={"knowledge_base_id": notebook_id, "name": "W2"}
    )

    resp = await client.get("/embed/widgets", headers=headers)
    assert resp.status_code == 200
    names = {w["name"] for w in resp.json()}
    assert names == {"W1", "W2"}


async def test_patch_widget_updates_name_origins_and_active(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-patch@test.com", "PatchOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets", headers=headers, json={"knowledge_base_id": notebook_id, "name": "Orig"}
    )
    widget_id = created.json()["id"]

    resp = await client.patch(
        f"/embed/widgets/{widget_id}",
        headers=headers,
        json={
            "name": "Renamed",
            "allowed_origins": ["https://example.com"],
            "is_active": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["allowed_origins"] == ["https://example.com"]
    assert body["is_active"] is False


async def test_delete_widget_removes_it(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-delete@test.com", "DeleteOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "ToDelete"},
    )
    widget_id = created.json()["id"]

    resp = await client.delete(f"/embed/widgets/{widget_id}", headers=headers)
    assert resp.status_code == 204

    listed = await client.get("/embed/widgets", headers=headers)
    assert widget_id not in [w["id"] for w in listed.json()]


async def test_create_widget_missing_notebook_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-missingnb@test.com", "MissingNbOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": str(uuid.uuid4()), "name": "W"},
    )
    assert resp.status_code == 404


async def test_member_forbidden_from_all_admin_widget_endpoints(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "embed-memberperm-owner@test.com", "MemberPermOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    notebook_id = (
        await client.post("/notebooks", headers=owner_headers, json={"name": "NB"})
    ).json()["id"]
    created = await client.post(
        "/embed/widgets",
        headers=owner_headers,
        json={"knowledge_base_id": notebook_id, "name": "W"},
    )
    widget_id = created.json()["id"]

    member_tokens = await _invite_member(client, owner_headers, "embed-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    create_resp = await client.post(
        "/embed/widgets",
        headers=member_headers,
        json={"knowledge_base_id": notebook_id, "name": "W2"},
    )
    assert create_resp.status_code == 403

    list_resp = await client.get("/embed/widgets", headers=member_headers)
    assert list_resp.status_code == 403

    patch_resp = await client.patch(
        f"/embed/widgets/{widget_id}", headers=member_headers, json={"name": "Hacked"}
    )
    assert patch_resp.status_code == 403

    delete_resp = await client.delete(f"/embed/widgets/{widget_id}", headers=member_headers)
    assert delete_resp.status_code == 403


async def test_cross_org_widget_404s_on_patch_and_delete(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "embed-isoa@test.com", "IsoOrgA")
    tokens_b = await _signup(client, "embed-isob@test.com", "IsoOrgB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (await client.post("/notebooks", headers=headers_a, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets", headers=headers_a, json={"knowledge_base_id": notebook_id, "name": "W"}
    )
    widget_id = created.json()["id"]

    patch_resp = await client.patch(
        f"/embed/widgets/{widget_id}", headers=headers_b, json={"name": "Stolen"}
    )
    assert patch_resp.status_code == 404

    delete_resp = await client.delete(f"/embed/widgets/{widget_id}", headers=headers_b)
    assert delete_resp.status_code == 404


# ---- Public config endpoint ----


async def test_public_config_valid_widget_returns_names(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-config@test.com", "ConfigOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook = await client.post("/notebooks", headers=headers, json={"name": "My Notebook"})
    notebook_id = notebook.json()["id"]
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "Config widget"},
    )
    public_id = created.json()["public_id"]

    resp = await client.get(f"/embed/public/{org_id}/{public_id}/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["widget_name"] == "Config widget"
    assert body["notebook_name"] == "My Notebook"


async def test_public_config_unknown_public_id_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-configmissing@test.com", "ConfigMissingOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    resp = await client.get(f"/embed/public/{org_id}/does-not-exist/config")
    assert resp.status_code == 404


async def test_public_config_revoked_widget_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-configrevoked@test.com", "ConfigRevokedOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets", headers=headers, json={"knowledge_base_id": notebook_id, "name": "W"}
    )
    public_id = created.json()["public_id"]
    widget_id = created.json()["id"]

    revoke = await client.patch(
        f"/embed/widgets/{widget_id}", headers=headers, json={"is_active": False}
    )
    assert revoke.status_code == 200

    resp = await client.get(f"/embed/public/{org_id}/{public_id}/config")
    assert resp.status_code == 404


async def test_public_config_wrong_org_id_404s(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "embed-wrongorg-a@test.com", "WrongOrgA")
    tokens_b = await _signup(client, "embed-wrongorg-b@test.com", "WrongOrgB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    org_id_b = await _org_id(client, headers_b)

    notebook_id = (await client.post("/notebooks", headers=headers_a, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets", headers=headers_a, json={"knowledge_base_id": notebook_id, "name": "W"}
    )
    public_id = created.json()["public_id"]

    # Correct public_id but a DIFFERENT (valid) org_id in the URL — must 404, not leak
    # into org A's widget.
    resp = await client.get(f"/embed/public/{org_id_b}/{public_id}/config")
    assert resp.status_code == 404


# ---- Public chat stream ----


async def test_public_stream_happy_path_persists_widget_conversation(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "embed-streamhappy@test.com", "StreamHappyOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content="widget content here"
    )
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "W", "allowed_origins": []},
    )
    public_id = created.json()["public_id"]

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=True)
    resp = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "widget", "parent_origin": "https://anywhere.example"},
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(token_events) >= 1
    assert len(done_events) == 1
    done = done_events[0]
    assert "[1]" in done["answer"]
    assert len(done["citations"]) == 1
    assert done["citations"][0]["document_id"] == str(doc_id)

    async with session_factory() as session:
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.id == uuid.UUID(done["conversation_id"]))
            )
        ).scalar_one()
    assert conversation.org_id == org_id
    assert str(conversation.knowledge_base_id) == notebook_id
    assert conversation.user_id is None
    assert str(conversation.widget_id) == created.json()["id"]


async def test_public_stream_missing_widget_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "embed-streammissing@test.com", "StreamMissingOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=True)
    resp = await client.post(
        f"/embed/public/{org_id}/does-not-exist/stream",
        json={"query": "q", "parent_origin": "https://anywhere.example"},
    )
    assert resp.status_code == 404


async def test_public_stream_disallowed_origin_403s(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "embed-streamorigin@test.com", "StreamOriginOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        ordinal=0,
        content="origin gated content",
    )
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={
            "knowledge_base_id": notebook_id,
            "name": "W",
            "allowed_origins": ["https://allowed.example"],
        },
    )
    public_id = created.json()["public_id"]

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=True)

    disallowed = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "q", "parent_origin": "https://evil.example"},
    )
    assert disallowed.status_code == 403

    allowed = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "q", "parent_origin": "https://allowed.example"},
    )
    assert allowed.status_code == 200


async def test_public_stream_empty_allowlist_allows_any_origin(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "embed-streamanyorigin@test.com", "StreamAnyOriginOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content="any origin content"
    )
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "W", "allowed_origins": []},
    )
    public_id = created.json()["public_id"]

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=True)
    resp = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "q", "parent_origin": "https://literally-anything.example"},
    )
    assert resp.status_code == 200


async def test_public_stream_rate_limited_returns_429(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "embed-streamratelimit@test.com", "StreamRateLimitOrg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]
    created = await client.post(
        "/embed/widgets",
        headers=headers,
        json={"knowledge_base_id": notebook_id, "name": "W", "allowed_origins": []},
    )
    public_id = created.json()["public_id"]

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=False)
    resp = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "q", "parent_origin": "https://anywhere.example"},
    )
    assert resp.status_code == 429


# ---- Anonymous-ctx access-controlling tag gating ----


async def test_anonymous_widget_visitor_excluded_from_tag_gated_document(
    client: AsyncClient, session_factory
) -> None:
    """Pins the previously-unexercised `resolve_user_granted_tags(user_id=None)`
    behavior: a document carrying an access-controlling tag (granted to SOME Access
    Role) must be invisible to the anonymous widget ctx, while an untagged document in
    the same notebook stays visible. Asserts BOTH directly against
    `resolve_allowed_documents` AND through the real public stream endpoint (the
    citation for a grounded answer must only ever point at the untagged document)."""
    owner_tokens = await _signup(client, "embed-taggate@test.com", "TagGateOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)

    open_doc = await _seed_document(session_factory, org_id, "Open doc")
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=open_doc,
        ordinal=0,
        content="publicly visible widget content",
        seed=1,
    )
    gated_doc = await _seed_document(session_factory, org_id, "Gated doc")
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=gated_doc,
        ordinal=0,
        content="restricted finance content",
        seed=2,
    )

    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "Finance-Only"})
    tag_id = tag.json()["id"]
    await client.post(f"/documents/{gated_doc}/tags/{tag_id}", headers=owner_headers)

    role = await client.post("/access-roles", headers=owner_headers, json={"name": "Finance Team"})
    role_id = role.json()["id"]
    grant = await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=owner_headers)
    assert grant.status_code == 204

    # Direct assertion (mirrors test_retrieval.py's precedent): the anonymous ctx has
    # no user_id, so it can never hold an Access Role — the gated doc is excluded, the
    # open one stays visible.
    anon_ctx = TenantContext(org_id=org_id, user_id=None, role=None)
    allowed = await resolve_allowed_documents(anon_ctx)
    assert gated_doc not in allowed
    assert open_doc in allowed

    notebook_id = (
        await client.post("/notebooks", headers=owner_headers, json={"name": "NB"})
    ).json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{open_doc}", headers=owner_headers)
    await client.post(f"/notebooks/{notebook_id}/documents/{gated_doc}", headers=owner_headers)
    created = await client.post(
        "/embed/widgets",
        headers=owner_headers,
        json={"knowledge_base_id": notebook_id, "name": "W", "allowed_origins": []},
    )
    public_id = created.json()["public_id"]

    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter(allow=True)
    resp = await client.post(
        f"/embed/public/{org_id}/{public_id}/stream",
        json={"query": "widget", "parent_origin": "https://anywhere.example"},
    )
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    done = next(e for e in events if e["type"] == "done")

    # Both documents live in the notebook, but the gated one must never surface — every
    # citation the anonymous stream returns must point at the OPEN document only.
    assert len(done["citations"]) >= 1
    cited_document_ids = {c["document_id"] for c in done["citations"]}
    assert cited_document_ids == {str(open_doc)}
