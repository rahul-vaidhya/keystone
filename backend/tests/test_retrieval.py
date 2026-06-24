"""F31 flat_vector retrieval: notebook scoping, tenant isolation (API-level and the
repository-level org_id backstop), active-model filtering, the allowed-documents stub,
and assemble_context's pure shape.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.documents.models import Document
from app.identity.models import Organization
from app.ingestion.models import Chunk, Embedding
from app.ingestion.schemas import ChunkHit
from app.ingestion.service import ingestion_service
from app.platform.context import TenantContext
from app.platform.seams import EMBED_DIM
from app.retrieval.service import assemble_context, resolve_allowed_documents
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
