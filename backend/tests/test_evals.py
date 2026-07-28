"""Evals domain: golden-question curation (offline, fake seams only — never imports
ragas, unlike the opt-in ``test_golden_eval.py``). Covers the ``GoldenQuestionRepository``
CRUD, the end-to-end curation flow via ``EvalsService.create_from_message``,
``require_admin`` gating on both endpoints, the "can't curate a user message" failure
path, not-found/cross-org 404s, and tenant isolation on the list endpoint.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import Document
from app.models.evals import GoldenQuestion
from app.models.ingestion import Chunk, Embedding
from app.services.evals import GoldenQuestionRepository, evals_service
from app.services.seams import EMBED_DIM
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


async def _make_notebook_with_document(
    client: AsyncClient, headers: dict, session_factory, org_id: uuid.UUID, content: str
) -> tuple[str, uuid.UUID]:
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content=content
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    return notebook_id, doc_id


# ---- GoldenQuestionRepository CRUD -----------------------------------------------


async def test_repository_create_and_list_active(session_factory, tenant_engine) -> None:
    org_id = uuid.uuid4()
    notebook_id = uuid.uuid4()
    ctx = TenantContext(org_id=org_id)
    async with db_mod.tenant_session(org_id) as session:
        # organizations + knowledge_bases FKs are required by the migration; seed
        # minimal rows directly.
        from app.models.auth import Organization
        from app.models.knowledge import Notebook

        session.add(Organization(id=org_id, name="RepoOrg"))
        await session.flush()
        session.add(Notebook(id=notebook_id, org_id=org_id, name="RepoNB"))
        await session.flush()
        repo = GoldenQuestionRepository(session, ctx)
        created = await repo.create(
            notebook_id=notebook_id,
            source_message_id=None,
            question="What is X?",
            reference_answer="X is Y.",
            reference_contexts=["ctx one", "ctx two"],
            created_by=None,
        )
        assert created.id is not None
        assert created.status == "active"

        active = await repo.list_active()
        assert [g.id for g in active] == [created.id]

        scoped = await repo.list_active(notebook_id=notebook_id)
        assert [g.id for g in scoped] == [created.id]

        other_notebook = await repo.list_active(notebook_id=uuid.uuid4())
        assert other_notebook == []


# ---- End-to-end curation ----------------------------------------------------------


async def test_create_from_message_end_to_end_matches_source(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "evals-e2e@test.com", "EvalsE2E")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    ask = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    assert ask.status_code == 200
    ask_body = ask.json()
    message_id = ask_body["message_id"]

    resp = await client.post(
        "/evals/golden-questions", headers=headers, json={"message_id": message_id}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["notebook_id"] == notebook_id
    assert body["source_message_id"] == message_id
    assert body["question"] == "alpha"
    assert body["reference_answer"] == ask_body["answer"]
    assert body["reference_contexts"] == ["alpha content about onboarding"]
    assert body["status"] == "active"

    # Also confirm the persisted row directly, not just the HTTP response shape.
    async with session_factory() as session:
        stored = (
            await session.execute(
                select(GoldenQuestion).where(GoldenQuestion.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
    assert stored.org_id == org_id
    assert stored.notebook_id == uuid.UUID(notebook_id)
    assert stored.question == "alpha"
    assert stored.reference_answer == ask_body["answer"]
    assert stored.reference_contexts == ["alpha content about onboarding"]

    # And via the service directly (not just the HTTP round-trip).
    me = (await client.get("/auth/me", headers=headers)).json()
    ctx = TenantContext(org_id=org_id, user_id=uuid.UUID(me["id"]), role=me["role"])
    out = await evals_service.list_golden_questions(ctx)
    assert len(out) == 1
    assert out[0].question == "alpha"


# ---- require_admin gating ----------------------------------------------------------


async def test_create_golden_question_requires_admin(client: AsyncClient, session_factory) -> None:
    owner_tokens = await _signup(client, "evals-admin-owner@test.com", "EvalsAdminOwner")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, owner_headers, session_factory, org_id, "member-gated content"
    )
    ask = await client.post(
        "/chat/ask", headers=owner_headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]

    member_tokens = await _invite_member(client, owner_headers, "evals-admin-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    resp = await client.post(
        "/evals/golden-questions", headers=member_headers, json={"message_id": message_id}
    )
    assert resp.status_code == 403


async def test_list_golden_questions_requires_admin(client: AsyncClient) -> None:
    owner_tokens = await _signup(client, "evals-list-owner@test.com", "EvalsListOwner")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}

    member_tokens = await _invite_member(client, owner_headers, "evals-list-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    resp = await client.get("/evals/golden-questions", headers=member_headers)
    assert resp.status_code == 403


# ---- Curating a user-role message fails --------------------------------------------


async def test_curate_user_role_message_400s(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "evals-usermsg@test.com", "EvalsUserMsg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "curation content"
    )
    ask = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    conversation_id = ask.json()["conversation_id"]

    from app.models.chat import Message as MessageRow

    async with session_factory() as session:
        user_message = (
            await session.execute(
                select(MessageRow).where(
                    MessageRow.conversation_id == uuid.UUID(conversation_id),
                    MessageRow.role == "user",
                )
            )
        ).scalar_one()
        user_message_id = user_message.id

    resp = await client.post(
        "/evals/golden-questions", headers=headers, json={"message_id": str(user_message_id)}
    )
    assert resp.status_code == 400
    assert "golden set" in resp.json()["detail"]


# ---- Not found / cross-org --------------------------------------------------------


async def test_curate_nonexistent_message_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "evals-missing@test.com", "EvalsMissing")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/evals/golden-questions", headers=headers, json={"message_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


async def test_curate_cross_org_message_404s(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "evals-isoa@test.com", "EvalsIsoA")
    tokens_b = await _signup(client, "evals-isob@test.com", "EvalsIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    org_id_a = await _org_id(client, headers_a)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers_a, session_factory, org_id_a, "org A content"
    )
    ask = await client.post(
        "/chat/ask", headers=headers_a, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]

    resp = await client.post(
        "/evals/golden-questions", headers=headers_b, json={"message_id": message_id}
    )
    assert resp.status_code == 404


# ---- Tenant isolation on the list endpoint -----------------------------------------


async def test_list_golden_questions_tenant_isolation(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "evals-list-isoa@test.com", "EvalsListIsoA")
    tokens_b = await _signup(client, "evals-list-isob@test.com", "EvalsListIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    org_id_a = await _org_id(client, headers_a)

    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers_a, session_factory, org_id_a, "org A only content"
    )
    ask = await client.post(
        "/chat/ask", headers=headers_a, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]
    curate = await client.post(
        "/evals/golden-questions", headers=headers_a, json={"message_id": message_id}
    )
    assert curate.status_code == 201

    resp_a = await client.get("/evals/golden-questions", headers=headers_a)
    resp_b = await client.get("/evals/golden-questions", headers=headers_b)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert len(resp_a.json()) == 1
    assert resp_b.json() == []
