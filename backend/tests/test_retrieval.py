"""F31 flat_vector retrieval: notebook scoping, tenant isolation (API-level and the
repository-level org_id backstop), active-model filtering, the allowed-documents stub,
and assemble_context's pure shape.
"""

from __future__ import annotations

import uuid

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.documents import Document
from app.models.ingestion import Chunk, ChunkHit, Embedding
from app.services.ingestion import ingestion_service
from app.services.retrieval import (
    assemble_context,
    fuse_rrf,
    resolve_allowed_documents,
    retrieval_service,
)
from app.services.seams import EMBED_DIM, FakeReranker, SeamTransientError
from app.utils.constants import ROLE_MEMBER
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


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


async def _invite_member(client: AsyncClient, owner_headers: dict, email: str) -> dict:
    """Invites a member into the owner's org and accepts the invite (self-serve link
    flow — the invitee sets their own password), returning their own token dict — the
    only way to get a real `member`-role TenantContext through the API rather than
    constructing one by hand."""
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


def _vector(seed: int, dim: int = EMBED_DIM) -> list[float]:
    """A cheap deterministic unit-ish vector — these tests only need presence/absence of
    hits (scope/isolation/model filtering), never a meaningful similarity ranking."""
    vec = [0.0] * dim
    vec[seed % dim] = 1.0
    return vec


async def _seed_document(session_factory, org_id: uuid.UUID, title: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title))
    return doc_id


async def _seed_document_in_folder(
    session_factory, org_id: uuid.UUID, title: str, folder_id: str
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title, folder_id=uuid.UUID(folder_id)))
    return doc_id


async def _seed_chunk_with_embedding(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    ordinal: int,
    content: str,
    model: str = FAKE_MODEL,
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
                model=model,
                dim=EMBED_DIM,
                embedding=_vector(seed),
            )
        )
    return chunk_id


async def test_search_scoped_to_notebook_documents(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "ret-scope@test.com", "Scope")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_in = await _seed_document(session_factory, org_id, "In notebook")
    doc_out = await _seed_document(session_factory, org_id, "Not in notebook")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_in, ordinal=0, content="alpha content"
    )
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_out, ordinal=0, content="beta content"
    )

    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_in}", headers=headers)

    resp = await client.post(
        "/retrieval/search",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "alpha"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["document_id"] == str(doc_in)
    assert body["results"][0]["index"] == 1


async def test_search_results_carry_page_range(client: AsyncClient, session_factory) -> None:
    """The Search page shows a page number on semantic cards too: ``/retrieval/search``
    hits carry the chat-citation page derivation (here: the parser's page marker)."""
    tokens = await _signup(client, "ret-pages@test.com", "Pages")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc = await _seed_document(session_factory, org_id, "Paged")
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc,
        ordinal=0,
        content="### Page 7" + chr(10) + "alpha",
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc}", headers=headers)

    resp = await client.post(
        "/retrieval/search", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    assert resp.status_code == 200
    [hit] = resp.json()["results"]
    assert hit["page_start"] == 7 and hit["page_end"] == 7
    assert hit["distance"] is not None


async def test_search_empty_notebook_returns_no_results(client: AsyncClient) -> None:
    tokens = await _signup(client, "ret-empty@test.com", "Empty")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    resp = await client.post(
        "/retrieval/search", headers=headers, json={"notebook_id": notebook_id, "query": "anything"}
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_search_k_bounds_are_validated(client: AsyncClient) -> None:
    tokens = await _signup(client, "ret-kbound@test.com", "KBound")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    too_low = await client.post(
        "/retrieval/search",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "q", "k": 0},
    )
    too_high = await client.post(
        "/retrieval/search",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "q", "k": 51},
    )
    assert too_low.status_code == 422
    assert too_high.status_code == 422


async def test_search_cross_org_notebook_404s(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "ret-isoa@test.com", "IsoA")
    tokens_b = await _signup(client, "ret-isob@test.com", "IsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (
        await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    ).json()["id"]

    resp = await client.post(
        "/retrieval/search", headers=headers_b, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 404


async def test_search_chunks_org_id_is_an_independent_backstop(
    session_factory, tenant_engine
) -> None:
    """Calls IngestionService.search_chunks directly (not via the API) with org A's
    TenantContext but org B's document_id in the requested scope. The API-level isolation
    test above can never reach this branch, because list_notebook_documents already 404s
    cross-org before search_chunks is ever called — this proves the WHERE org_id=:org
    predicate INSIDE search_chunks independently blocks the leak, so removing it later
    would be caught here even if every upstream scoping check were somehow bypassed."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        session.add(Document(id=uuid.uuid4(), org_id=org_a, title="A"))
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    chunk_id = await _seed_chunk_with_embedding(
        session_factory, org_id=org_b, document_id=doc_b, ordinal=0, content="org b secret"
    )

    ctx_a = TenantContext(org_id=org_a)
    hits = await ingestion_service.search_chunks(
        ctx_a,
        query_vector=_vector(0),
        document_ids=[doc_b],
        model=FAKE_MODEL,
        k=8,
    )
    assert hits == []

    # control: org B's own context CAN see it, proving the absence above is the filter,
    # not e.g. a query bug that returns nothing for anyone.
    ctx_b = TenantContext(org_id=org_b)
    control_hits = await ingestion_service.search_chunks(
        ctx_b, query_vector=_vector(0), document_ids=[doc_b], model=FAKE_MODEL, k=8
    )
    assert [hit.chunk_id for hit in control_hits] == [chunk_id]


async def test_get_chunks_org_id_is_an_independent_backstop(session_factory, tenant_engine) -> None:
    """``ingestion_service.get_chunks`` (F41's citation-resolution entry point) is a
    by-id fetch, not a scope-derived search — exactly where a missing org filter would
    leak cross-tenant chunk content into another org's citations. Calls it directly with
    org A's TenantContext but org B's chunk_id, mirroring
    ``test_search_chunks_org_id_is_an_independent_backstop`` above."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    chunk_id = await _seed_chunk_with_embedding(
        session_factory, org_id=org_b, document_id=doc_b, ordinal=0, content="org b secret"
    )

    ctx_a = TenantContext(org_id=org_a)
    records = await ingestion_service.get_chunks(ctx_a, [chunk_id])
    assert records == []

    # control: org B's own context CAN see it, proving the absence above is the filter,
    # not e.g. a query bug that returns nothing for anyone.
    ctx_b = TenantContext(org_id=org_b)
    control_records = await ingestion_service.get_chunks(ctx_b, [chunk_id])
    assert [record.chunk_id for record in control_records] == [chunk_id]


async def test_active_model_filter_excludes_other_models(session_factory, tenant_engine) -> None:
    """Re-embedding the same chunk under a second model name (same upsert idempotency
    contract as F22) must not produce a duplicate/cross-model hit when searching under the
    FIRST model — exactly one hit per chunk per active model."""
    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="Org"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Doc"))
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=doc_id,
                section_id=None,
                ordinal=0,
                content="some content",
                token_count=3,
                char_start=0,
                char_end=12,
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=doc_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model="model-a",
                dim=EMBED_DIM,
                embedding=_vector(1),
            )
        )
        session.add(
            Embedding(
                org_id=org_id,
                document_id=doc_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model="model-b",
                dim=EMBED_DIM,
                embedding=_vector(2),
            )
        )

    ctx = TenantContext(org_id=org_id)
    hits = await ingestion_service.search_chunks(
        ctx, query_vector=_vector(1), document_ids=[doc_id], model="model-a", k=8
    )
    assert [hit.chunk_id for hit in hits] == [chunk_id]


async def test_resolve_allowed_documents_returns_all_org_documents(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "ret-allowed@test.com", "Allowed")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc_1 = await _seed_document(session_factory, org_id, "One")
    doc_2 = await _seed_document(session_factory, org_id, "Two")

    allowed = await resolve_allowed_documents(TenantContext(org_id=org_id))
    assert set(allowed) == {doc_1, doc_2}


async def test_untagged_documents_stay_open_to_members(
    client: AsyncClient, session_factory
) -> None:
    owner_tokens = await _signup(client, "ret-open-owner@test.com", "OpenOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    member_ctx = TenantContext(org_id=org_id, user_id=None, role=ROLE_MEMBER)

    doc = await _seed_document(session_factory, org_id, "Public memo")

    allowed = await resolve_allowed_documents(member_ctx)
    assert doc in allowed


async def test_tag_granted_to_no_role_stays_open(client: AsyncClient, session_factory) -> None:
    """A document tagged with an ordinary organizational tag that no Access Role has
    ever been granted stays open — tagging alone doesn't gate anything."""
    owner_tokens = await _signup(client, "ret-ungranted-owner@test.com", "UngrantedOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    member_ctx = TenantContext(org_id=org_id, user_id=None, role=ROLE_MEMBER)

    doc = await _seed_document(session_factory, org_id, "Q1 report")
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "Q1"})
    tag_id = tag.json()["id"]
    await client.post(f"/documents/{doc}/tags/{tag_id}", headers=owner_headers)

    allowed = await resolve_allowed_documents(member_ctx)
    assert doc in allowed


async def test_access_controlling_document_tag_gates_by_role(
    client: AsyncClient, session_factory
) -> None:
    owner_tokens = await _signup(client, "ret-doctag-owner@test.com", "DocTagOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)

    doc = await _seed_document(session_factory, org_id, "Finance memo")
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "Finance"})
    tag_id = tag.json()["id"]
    await client.post(f"/documents/{doc}/tags/{tag_id}", headers=owner_headers)

    role = await client.post("/access-roles", headers=owner_headers, json={"name": "Finance Team"})
    role_id = role.json()["id"]
    grant = await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=owner_headers)
    assert grant.status_code == 204

    outsider_ctx = TenantContext(org_id=org_id, user_id=uuid.uuid4(), role=ROLE_MEMBER)
    assert doc not in await resolve_allowed_documents(outsider_ctx)

    member_tokens = await _invite_member(client, owner_headers, "ret-doctag-member@test.com")
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {member_tokens['access_token']}"}
    )
    member_user_id = uuid.UUID(me.json()["id"])

    member_ctx_before = TenantContext(org_id=org_id, user_id=member_user_id, role=ROLE_MEMBER)
    assert doc not in await resolve_allowed_documents(member_ctx_before)

    assign = await client.post(
        f"/access-roles/{role_id}/users/{member_user_id}", headers=owner_headers
    )
    assert assign.status_code == 204

    member_ctx_after = TenantContext(org_id=org_id, user_id=member_user_id, role=ROLE_MEMBER)
    assert doc in await resolve_allowed_documents(member_ctx_after)

    owner_ctx = TenantContext(org_id=org_id, user_id=None, role="owner")
    assert doc in await resolve_allowed_documents(owner_ctx)


async def test_access_controlling_folder_tag_inherits_to_subtree(
    client: AsyncClient, session_factory
) -> None:
    owner_tokens = await _signup(client, "ret-foldertag-owner@test.com", "FolderTagOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)

    parent = await client.post("/documents/folders", headers=owner_headers, json={"name": "HR"})
    parent_id = parent.json()["id"]
    child = await client.post(
        "/documents/folders",
        headers=owner_headers,
        json={"name": "Payroll", "parent_id": parent_id},
    )
    child_id = child.json()["id"]
    doc_in_child = await _seed_document_in_folder(session_factory, org_id, "Payslip", child_id)

    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "HR-Only"})
    tag_id = tag.json()["id"]
    tag_folder = await client.post(
        f"/documents/folders/{parent_id}/tags/{tag_id}", headers=owner_headers
    )
    assert tag_folder.status_code == 204

    role = await client.post("/access-roles", headers=owner_headers, json={"name": "HR Team"})
    role_id = role.json()["id"]
    await client.post(f"/access-roles/{role_id}/tags/{tag_id}", headers=owner_headers)

    member_tokens = await _invite_member(client, owner_headers, "ret-foldertag-member@test.com")
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {member_tokens['access_token']}"}
    )
    member_user_id = uuid.UUID(me.json()["id"])
    member_ctx = TenantContext(org_id=org_id, user_id=member_user_id, role=ROLE_MEMBER)

    # Tagging the PARENT gates the child's document too, even though the child folder
    # and the document itself were never directly tagged.
    assert doc_in_child not in await resolve_allowed_documents(member_ctx)

    await client.post(f"/access-roles/{role_id}/users/{member_user_id}", headers=owner_headers)
    assert doc_in_child in await resolve_allowed_documents(member_ctx)


async def test_folder_tagging_requires_admin(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "ret-foldertagperm-owner@test.com", "FolderTagPermOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite_member(client, owner_headers, "ret-foldertagperm-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    folder = await client.post("/documents/folders", headers=owner_headers, json={"name": "X"})
    folder_id = folder.json()["id"]
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "T"})
    tag_id = tag.json()["id"]

    forbidden = await client.post(
        f"/documents/folders/{folder_id}/tags/{tag_id}", headers=member_headers
    )
    assert forbidden.status_code == 403

    allowed = await client.post(
        f"/documents/folders/{folder_id}/tags/{tag_id}", headers=owner_headers
    )
    assert allowed.status_code == 204


async def test_document_tagging_stays_open_to_any_member(
    client: AsyncClient, session_factory
) -> None:
    """Unlike folder tagging, tagging a plain document is unchanged from before this
    feature — no admin gate."""
    owner_tokens = await _signup(client, "ret-doctagperm-owner@test.com", "DocTagPermOrg")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    member_tokens = await _invite_member(client, owner_headers, "ret-doctagperm-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    doc_id = await _seed_document(session_factory, org_id, "Doc")
    tag = await client.post("/documents/tags", headers=owner_headers, json={"name": "T"})
    tag_id = tag.json()["id"]

    resp = await client.post(f"/documents/{doc_id}/tags/{tag_id}", headers=member_headers)
    assert resp.status_code == 204


def test_assemble_context_numbers_hits_with_source_refs() -> None:
    document_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    hits = [
        ChunkHit(
            chunk_id=chunk_id,
            document_id=document_id,
            content="hello",
            char_start=0,
            char_end=5,
            distance=0.1,
        )
    ]

    response = assemble_context("a query", hits)

    assert response.query == "a query"
    assert len(response.results) == 1
    block = response.results[0]
    assert block.index == 1
    assert block.document_id == document_id
    assert block.chunk_id == chunk_id
    assert block.char_start == 0
    assert block.char_end == 5
    assert block.content == "hello"


def test_assemble_context_empty_hits_returns_empty_results() -> None:
    response = assemble_context("q", [])
    assert response.results == []


# --- Reranker (RERANKER_ENABLED gate) --------------------------------------------------


class _RecordingReranker:
    """Test double that records how many candidates it actually received (before any
    truncation to top_k) — the only reliable way to prove the chunk kNN pool was
    genuinely WIDENED to RERANK_CANDIDATE_K, since with all-orthogonal fake-embedding
    distances the post-truncation ORDER alone can't distinguish "widened then truncated"
    from "never widened"."""

    def __init__(self) -> None:
        self.received_candidate_count: int | None = None

    async def rerank(self, query: str, candidates: list[ChunkHit], top_k: int) -> list[ChunkHit]:
        self.received_candidate_count = len(candidates)
        return [hit.model_copy(update={"rerank_score": 1.0}) for hit in candidates[:top_k]]


async def _seed_org_and_document(session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="RerankOrg"))
        await session.flush()
        session.add(Document(id=doc_id, org_id=org_id, title="Doc"))
    return org_id, doc_id


async def test_reranker_gate_off_regression_matches_pre_feature_behavior(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=False (the default): _retrieve_hits is byte-identical to before
    this feature existed — same count, same order, reranker.rerank is NEVER called (proven
    by _RecordingReranker never recording a call), and rerank_score is None on every
    result."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    org_id, doc_id = await _seed_org_and_document(session_factory)
    for i in range(3):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i,
            content=f"chunk {i}",
            seed=i,
        )

    ctx = TenantContext(org_id=org_id)
    recorder = _RecordingReranker()
    hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=2,
        reranker=recorder,
        query="a query",
    )
    assert len(hits) == 2  # unwidened: req.k passed straight to the SQL LIMIT
    assert all(hit.rerank_score is None for hit in hits)
    assert recorder.received_candidate_count is None  # reranker.rerank was never called


async def test_reranker_gate_on_widens_candidate_pool_and_truncates_final_k(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=True: the chunk kNN pool widens to
    candidate_k = max(req.k, RERANK_CANDIDATE_K) BEFORE reranking (proven directly by
    _RecordingReranker.received_candidate_count), and the final result is truncated to
    final_k = min(req.k, RERANK_TOP_K) AFTER reranking."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_K", 5)
    monkeypatch.setattr(settings, "RERANK_TOP_K", 3)

    org_id, doc_id = await _seed_org_and_document(session_factory)
    # 6 distinct chunks so candidate_k=5 (widened) is observably less than the available
    # pool, and strictly greater than req.k=2 (would be the unwidened count).
    for i in range(6):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i,
            content=f"chunk {i}",
            seed=i,
        )

    ctx = TenantContext(org_id=org_id)
    recorder = _RecordingReranker()
    req_k = 2
    hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=req_k,
        reranker=recorder,
        query="a query",
    )
    assert recorder.received_candidate_count == 5  # max(req_k=2, RERANK_CANDIDATE_K=5)
    assert len(hits) == 2  # min(req_k=2, RERANK_TOP_K=3)


async def test_reranker_gate_on_stamps_rerank_score_via_fake_reranker(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=True + RERANKER_MODE=fake (the DoD's exact scenario): every
    returned result carries a non-None rerank_score, sourced through the real
    FakeReranker (not a test double)."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_MODE", "fake")

    org_id, doc_id = await _seed_org_and_document(session_factory)
    for i in range(3):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i,
            content=f"chunk {i}",
            seed=i,
        )

    ctx = TenantContext(org_id=org_id)
    hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=3,
        reranker=FakeReranker(),
        query="a query",
    )
    assert hits, "expected at least one hit"
    assert all(hit.rerank_score is not None for hit in hits)


async def test_reranker_gate_off_context_block_rerank_score_is_none_over_http(
    client: AsyncClient, session_factory
) -> None:
    """End-to-end HTTP proof (default settings — gate off): the new `rerank_score` field
    on ContextBlock is present and None, exercising the real controller Depends(get_reranker)
    wiring, not just the service layer directly."""
    tokens = await _signup(client, "rerank-http-off@test.com", "RerankHttpOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)

    doc = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc, ordinal=0, content="alpha content"
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc}", headers=headers)

    resp = await client.post(
        "/retrieval/search",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "alpha"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["rerank_score"] is None


class _FailingReranker:
    """Test double that always raises ``SeamTransientError``, simulating a timed-out or
    unreachable reranker HTTP call — proves ``_retrieve_hits`` degrades to the unreranked
    candidates instead of failing the whole request."""

    async def rerank(self, query: str, candidates: list[ChunkHit], top_k: int) -> list[ChunkHit]:
        raise SeamTransientError("simulated reranker timeout")


async def test_reranker_transient_failure_falls_back_to_unreranked_hits(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient reranker failure (timeout, connection error, 5xx — anything the real
    seam wraps as SeamTransientError) must not fail the chat turn: _retrieve_hits catches
    it and falls back to the pre-rerank candidates, truncated to final_k, with every
    rerank_score staying None (never fabricated)."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_K", 5)
    monkeypatch.setattr(settings, "RERANK_TOP_K", 3)

    org_id, doc_id = await _seed_org_and_document(session_factory)
    for i in range(5):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i,
            content=f"chunk {i}",
            seed=i,
        )

    ctx = TenantContext(org_id=org_id)
    hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=2,
        reranker=_FailingReranker(),
        query="a query",
    )
    assert len(hits) == 2  # final_k = min(req_k=2, RERANK_TOP_K=3)
    assert all(hit.rerank_score is None for hit in hits)


async def test_reranker_transient_failure_logs_fallback_at_info(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback path logs at INFO (same visibility precedent as
    retrieval.hierarchical_fallback_no_sections/no_chunks) so a production reranker outage
    is observable, not silent."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)

    org_id, doc_id = await _seed_org_and_document(session_factory)
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content="chunk 0"
    )

    ctx = TenantContext(org_id=org_id)
    with structlog.testing.capture_logs() as cap_logs:
        await retrieval_service._retrieve_hits(
            ctx,
            query_vector=_vector(0),
            scope=[doc_id],
            model=FAKE_MODEL,
            k=1,
            reranker=_FailingReranker(),
            query="a query",
        )
    assert any(e["event"] == "retrieval.reranker_fallback" for e in cap_logs)


async def test_reranker_non_transient_failure_still_propagates(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-transient reranker exception (a real bug, not a classified timeout/5xx) must
    NOT be silently swallowed by the fallback — only SeamTransientError degrades."""

    class _BuggyReranker:
        async def rerank(
            self, query: str, candidates: list[ChunkHit], top_k: int
        ) -> list[ChunkHit]:
            raise ValueError("not a seam-classified failure")

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    org_id, doc_id = await _seed_org_and_document(session_factory)
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content="chunk 0"
    )

    ctx = TenantContext(org_id=org_id)
    with pytest.raises(ValueError, match="not a seam-classified failure"):
        await retrieval_service._retrieve_hits(
            ctx,
            query_vector=_vector(0),
            scope=[doc_id],
            model=FAKE_MODEL,
            k=1,
            reranker=_BuggyReranker(),
            query="a query",
        )


# --- Hybrid search (HYBRID_SEARCH_ENABLED gate) ---------------------------------------


def _rrf_hit(chunk_id: uuid.UUID, *, distance: float | None = None) -> ChunkHit:
    """A minimal ChunkHit for fuse_rrf's pure-function tests — content/offsets/
    document_id are irrelevant to RRF's rank-based scoring, only chunk_id (the dedup
    key) and distance (None = lexical-only) matter."""
    return ChunkHit(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        content="irrelevant",
        char_start=0,
        char_end=1,
        distance=distance,
    )


def test_fuse_rrf_vector_only_returns_vector_order() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector_hits = [_rrf_hit(a, distance=0.1), _rrf_hit(b, distance=0.2), _rrf_hit(c, distance=0.3)]
    fused = fuse_rrf(vector_hits, [])
    assert [h.chunk_id for h in fused] == [a, b, c]


def test_fuse_rrf_lexical_only_returns_lexical_order() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    lexical_hits = [_rrf_hit(a), _rrf_hit(b)]
    fused = fuse_rrf([], lexical_hits)
    assert [h.chunk_id for h in fused] == [a, b]


def test_fuse_rrf_overlapping_hit_scores_boosted_and_appears_once() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    vector_hits = [_rrf_hit(a, distance=0.1), _rrf_hit(b, distance=0.2)]
    lexical_hits = [_rrf_hit(a)]
    fused = fuse_rrf(vector_hits, lexical_hits)
    ids = [h.chunk_id for h in fused]
    assert ids.count(a) == 1
    # `a` appears in both lists (rank 1 in each) -- its summed score must outrank `b`,
    # which appears only in the vector list.
    assert fused[0].chunk_id == a


def test_fuse_rrf_both_empty_returns_empty_list() -> None:
    assert fuse_rrf([], []) == []


def test_fuse_rrf_exposes_fused_score_and_per_list_ranks() -> None:
    """The fused order is explained on each hit: RRF score = sum of 1/(60 + rank) over the
    lists the chunk is in, plus its rank in each list (None when absent)."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector_hits = [_rrf_hit(b, distance=0.1), _rrf_hit(a, distance=0.2)]
    lexical_hits = [_rrf_hit(a), _rrf_hit(c)]
    by_id = {h.chunk_id: h for h in fuse_rrf(vector_hits, lexical_hits)}
    assert (by_id[a].vector_rank, by_id[a].lexical_rank) == (2, 1)
    assert by_id[a].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert (by_id[b].vector_rank, by_id[b].lexical_rank) == (1, None)
    assert by_id[b].fused_score == pytest.approx(1 / 61)
    assert (by_id[c].vector_rank, by_id[c].lexical_rank) == (None, 2)
    scores = [h.fused_score for h in fuse_rrf(vector_hits, lexical_hits)]
    assert scores == sorted(scores, reverse=True)


def test_fuse_rrf_double_top_rank_outranks_single_top_rank() -> None:
    """A chunk ranked #1 in BOTH lists outranks a chunk ranked #1 in only one list."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector_hits = [_rrf_hit(a, distance=0.1), _rrf_hit(b, distance=0.2)]
    lexical_hits = [_rrf_hit(a), _rrf_hit(c)]
    fused = fuse_rrf(vector_hits, lexical_hits)
    assert fused[0].chunk_id == a


async def test_search_chunks_lexical_finds_rare_term_and_scopes_by_org(
    session_factory, tenant_engine
) -> None:
    """Repository-level test against a REAL Testcontainers Postgres -- exercises the
    actual GIN index + websearch_to_tsquery/ts_rank SQL (not mockable). Also confirms
    the generated `content_tsv` column is populated and searchable for a chunk inserted
    fresh against the already-migrated schema (migration 0021 ran once for the whole
    Testcontainers session before any test's rows exist)."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="LexOrgA"))
        session.add(Organization(id=org_b, name="LexOrgB"))
        await session.flush()
        doc_a = uuid.uuid4()
        session.add(Document(id=doc_a, org_id=org_a, title="A"))
        doc_b = uuid.uuid4()
        session.add(Document(id=doc_b, org_id=org_b, title="B"))

    rare_chunk_id = await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_a,
        document_id=doc_a,
        ordinal=0,
        content="The device shipped with a rare component called Zylophone-9000 attached.",
    )
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_a,
        document_id=doc_a,
        ordinal=1,
        content="A generic paragraph about nothing in particular.",
        seed=1,
    )
    # A chunk in a DIFFERENT org, also containing the rare term -- must never be
    # returned when searching in org_a's scope (tenant isolation, independent backstop).
    await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_b,
        document_id=doc_b,
        ordinal=0,
        content="Another Zylophone-9000 mention, but in a different org entirely.",
        seed=2,
    )

    ctx_a = TenantContext(org_id=org_a)
    hits = await ingestion_service.search_chunks_lexical(
        ctx_a, query="Zylophone-9000", document_ids=[doc_a, doc_b], k=10
    )
    assert [h.chunk_id for h in hits] == [rare_chunk_id]
    assert hits[0].distance is None

    # Empty document_ids returns [] immediately, mirroring search_chunks.
    empty_hits = await ingestion_service.search_chunks_lexical(
        ctx_a, query="Zylophone-9000", document_ids=[], k=10
    )
    assert empty_hits == []

    # Control: org B's own context CAN see its own chunk -- proving the absence above
    # is the org filter, not e.g. a query bug that returns nothing for anyone.
    ctx_b = TenantContext(org_id=org_b)
    control_hits = await ingestion_service.search_chunks_lexical(
        ctx_b, query="Zylophone-9000", document_ids=[doc_b], k=10
    )
    assert len(control_hits) == 1


async def test_hybrid_gate_off_never_calls_lexical_search_and_matches_pre_feature_behavior(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HYBRID_SEARCH_ENABLED=False (the default): _retrieve_hits never calls
    search_chunks_lexical (proven by making it raise if called) and results are
    identical in count/shape to before this feature existed."""
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", False)

    async def _boom(*args, **kwargs):
        raise AssertionError(
            "search_chunks_lexical must never be called when HYBRID_SEARCH_ENABLED=False"
        )

    monkeypatch.setattr(ingestion_service, "search_chunks_lexical", _boom)

    org_id, doc_id = await _seed_org_and_document(session_factory)
    for i in range(3):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i,
            content=f"chunk {i}",
            seed=i,
        )

    ctx = TenantContext(org_id=org_id)
    recorder = _RecordingReranker()
    hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=_vector(0),
        scope=[doc_id],
        model=FAKE_MODEL,
        k=2,
        reranker=recorder,
        query="a query",
    )
    assert len(hits) == 2  # unwidened: req.k passed straight to the SQL LIMIT
    assert recorder.received_candidate_count is None  # reranker.rerank was never called


async def test_hybrid_gate_on_finds_lexical_match_pure_vector_search_misses(
    session_factory, tenant_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core proof hybrid search exists for. A chunk containing an exact rare term
    ("Zylophone-9000") is embedded with a vector ORTHOGONAL to the query vector (cosine
    distance ~1.0 -- would never surface via pure vector kNN at a small k), while decoy
    chunks are embedded IDENTICAL to the query vector (cosine distance 0.0 -- always win
    pure vector kNN). First confirms the gate-OFF baseline genuinely misses the target;
    then confirms gate-ON finds it via the lexical candidate path, fused in via RRF."""
    org_id, doc_id = await _seed_org_and_document(session_factory)
    query_vec = _vector(0)
    target_chunk_id = await _seed_chunk_with_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        ordinal=0,
        content="The device shipped with a rare component called Zylophone-9000 attached.",
        seed=1,  # orthogonal to query_vec == _vector(0)
    )
    for i in range(3):
        await _seed_chunk_with_embedding(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            ordinal=i + 1,
            content=f"generic filler paragraph number {i}",
            seed=0,  # identical to query_vec -- always wins pure vector kNN
        )

    ctx = TenantContext(org_id=org_id)
    recorder = _RecordingReranker()

    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", False)
    vector_only_hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=query_vec,
        scope=[doc_id],
        model=FAKE_MODEL,
        k=1,
        reranker=recorder,
        query="Zylophone-9000",
    )
    assert target_chunk_id not in [h.chunk_id for h in vector_only_hits]

    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "HYBRID_CANDIDATE_K", 5)
    hybrid_hits = await retrieval_service._retrieve_hits(
        ctx,
        query_vector=query_vec,
        scope=[doc_id],
        model=FAKE_MODEL,
        k=1,
        reranker=recorder,
        query="Zylophone-9000",
    )
    assert target_chunk_id in [h.chunk_id for h in hybrid_hits]
