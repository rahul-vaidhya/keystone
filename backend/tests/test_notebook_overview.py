"""P1 Notebook Overview (memory.md "P1 roadmap", feature 3 of 3): the on-demand, cached
"gist of everything" artifact per notebook. Reuses the SAME map-reduce mechanism
(``app.services.retrieval.mapreduce``) feature 1's chat broad-query router already
exercises (``tests/test_broad_query.py``) — this file focuses on what's new: caching/
upsert semantics, the stale-on-attach/detach hook, the ``NOTEBOOK_OVERVIEW_ENABLED``
gate, and notebook-level (not Access-Role) access control.

Uses the FAKE LLM/embedder seams exclusively — deterministic, no network, no API keys.
``NOTEBOOK_OVERVIEW_ENABLED`` defaults to False, so generation is refused unless a test
explicitly enables it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.middleware.context import TenantContext
from app.models.auth import Organization
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding, Section
from app.models.knowledge import Notebook, NotebookOverview
from app.services.seams import EMBED_DIM, Message, get_llm
from app.utils.constants import ROLE_MEMBER
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


def _vector(seed: int, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    vec[seed % dim] = 1.0
    return vec


class _OverviewLLM:
    """Test double distinguishing the map/reduce call shapes by a substring in the
    SYSTEM prompt — same precedent as ``test_broad_query.py``'s ``_BroadRoutingLLM``,
    minus the classifier branch (Notebook Overview never classifies — it always runs
    map-reduce once ``is_broad_query_available`` passes)."""

    def __init__(self) -> None:
        self.map_calls = 0
        self.reduce_calls = 0

    @property
    def model(self) -> str:
        return "overview-llm"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        system = messages[0].content
        user = messages[1].content
        if "extract information relevant to a question from one section" in system:
            self.map_calls += 1
            heading_line = next(
                (line for line in user.splitlines() if line.startswith("Section heading:")),
                "Section heading: ?",
            )
            text = f"extract about {heading_line.split(':', 1)[1].strip()}"
        else:
            self.reduce_calls += 1
            markers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", user)})
            text = "Overview synthesis " + " ".join(f"[{n}]" for n in markers)
        for token in text.split(" "):
            yield token + " "


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_llm, None)


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


async def _org_id(client: AsyncClient, headers: dict) -> uuid.UUID:
    me = await client.get("/auth/me", headers=headers)
    return uuid.UUID(me.json()["org_id"])


async def _seed_document(session_factory, org_id: uuid.UUID, title: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title))
    return doc_id


async def _seed_section_with_summary(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    heading: str,
    summary: str,
    chunk_content: str = "chunk content",
    seed: int = 0,
) -> uuid.UUID:
    """Seeds one ``Section`` carrying a V2 enrichment summary, plus one ``Chunk`` +
    ``Embedding`` under it — the exact shape ``is_broad_query_available``/map-reduce
    reads (mirrors ``test_broad_query.py``'s helper of the same name)."""
    section_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Section(
                id=section_id,
                org_id=org_id,
                document_id=document_id,
                parent_section_id=None,
                ordinal=0,
                depth=1,
                path="1",
                heading=heading,
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=len(chunk_content),
                summary=summary,
                topics=None,
            )
        )
        await session.flush()
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                section_id=section_id,
                ordinal=0,
                content=chunk_content,
                token_count=max(len(chunk_content) // 4, 1),
                char_start=0,
                char_end=len(chunk_content),
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
    return section_id


async def _make_notebook_with_summary(
    client: AsyncClient, session_factory, headers: dict, org_id: uuid.UUID
) -> tuple[str, uuid.UUID]:
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    section_id = await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        heading="Overview Section",
        summary="This section covers everything.",
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    return notebook_id, section_id


# ---- Happy path: generate / cache / re-fetch / regenerate --------------------------


async def test_generate_produces_cached_row_with_citations(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-happy@test.com", "OvHappy")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    llm = _OverviewLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert llm.map_calls == 1
    assert llm.reduce_calls == 1
    assert "[1]" in body["content"]
    assert body["stale"] is False
    assert body["source_document_count"] == 1
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["citation_type"] == "section"
    assert citation["section_id"] == str(section_id)
    assert citation["chunk_id"] is None

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(NotebookOverview).where(
                        NotebookOverview.notebook_id == uuid.UUID(notebook_id)
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1


async def test_get_overview_returns_cached_row_unchanged(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-refetch@test.com", "OvRefetch")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    gen_resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert gen_resp.status_code == 200
    generated = gen_resp.json()

    fetch_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert fetch_resp.status_code == 200
    fetched = fetch_resp.json()
    assert fetched == generated


async def test_regenerate_overwrites_same_row_not_a_duplicate(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-regen@test.com", "OvRegen")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    first = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert first.status_code == 200
    first_id = first.json()["id"]
    first_generated_at = first.json()["generated_at"]

    # Add a second summarized section so the regenerated content differs.
    doc2 = await _seed_document(session_factory, org_id, "Doc2")
    await _seed_section_with_summary(
        session_factory,
        org_id=org_id,
        document_id=doc2,
        heading="Second Section",
        summary="More content.",
        chunk_content="second content",
        seed=5,
    )
    await client.post(f"/notebooks/{notebook_id}/documents/{doc2}", headers=headers)

    second = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["id"] == first_id  # same row, upserted in place
    assert second_body["source_document_count"] == 2
    assert second_body["generated_at"] != first_generated_at
    assert second_body["stale"] is False

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(NotebookOverview).where(
                        NotebookOverview.notebook_id == uuid.UUID(notebook_id)
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1


# ---- Stale-on-attach/detach hook ----------------------------------------------------


async def test_attach_document_marks_existing_overview_stale(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-stale-attach@test.com", "OvStaleAttach")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    gen = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert gen.json()["stale"] is False

    doc2 = await _seed_document(session_factory, org_id, "AnotherDoc")
    attach_resp = await client.post(f"/notebooks/{notebook_id}/documents/{doc2}", headers=headers)
    assert attach_resp.status_code == 204

    fetch_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert fetch_resp.json()["stale"] is True


async def test_detach_document_marks_existing_overview_stale(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-stale-detach@test.com", "OvStaleDetach")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_section_with_summary(
        session_factory, org_id=org_id, document_id=doc_id, heading="H", summary="S"
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    gen = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert gen.json()["stale"] is False

    detach_resp = await client.delete(
        f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers
    )
    assert detach_resp.status_code == 204

    fetch_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert fetch_resp.json()["stale"] is True


async def test_regenerate_clears_stale(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-clear-stale@test.com", "OvClearStale")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)

    doc2 = await _seed_document(session_factory, org_id, "AnotherDoc")
    await client.post(f"/notebooks/{notebook_id}/documents/{doc2}", headers=headers)
    assert (await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)).json()[
        "stale"
    ] is True

    regen = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert regen.json()["stale"] is False


# ---- Refusal / fallback paths --------------------------------------------------------


async def test_generate_refuses_when_zero_section_summaries(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-nosum@test.com", "OvNoSum")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    llm = _OverviewLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert resp.status_code == 409
    assert llm.map_calls == 0
    assert llm.reduce_calls == 0


async def test_generate_refuses_when_document_count_exceeds_cap(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "BROAD_QUERY_MAX_DOCUMENTS", 1)
    tokens = await _signup(client, "ov-toomany@test.com", "OvTooMany")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    for i in range(2):
        doc_id = await _seed_document(session_factory, org_id, f"Doc{i}")
        await _seed_section_with_summary(
            session_factory,
            org_id=org_id,
            document_id=doc_id,
            heading=f"Heading{i}",
            summary=f"Summary {i}",
            chunk_content=f"content number {i}",
            seed=i + 1,
        )
        await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    llm = _OverviewLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert resp.status_code == 409
    assert llm.map_calls == 0


async def test_generate_refuses_when_flag_disabled(client: AsyncClient, session_factory) -> None:
    """``NOTEBOOK_OVERVIEW_ENABLED`` defaults False — generation refuses even with valid
    section summaries present, and never touches the LLM at all."""
    tokens = await _signup(client, "ov-flagoff@test.com", "OvFlagOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    llm = _OverviewLLM()
    app.dependency_overrides[get_llm] = lambda: llm

    resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert resp.status_code == 409
    assert llm.map_calls == 0
    assert llm.reduce_calls == 0


async def test_get_overview_404_before_any_generation(client: AsyncClient) -> None:
    tokens = await _signup(client, "ov-neverGen@test.com", "OvNeverGen")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert resp.status_code == 404


async def test_get_overview_still_works_after_flag_disabled_post_generation(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning the flag off blocks NEW generation, but doesn't hide an
    already-generated overview — same philosophy as RERANKER_ENABLED off not erasing
    already-stored rerank_score values."""
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens = await _signup(client, "ov-flagofflater@test.com", "OvFlagOffLater")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, headers, org_id
    )

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    gen = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert gen.status_code == 200

    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", False)
    fetch_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert fetch_resp.status_code == 200

    regen_resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers)
    assert regen_resp.status_code == 409


# ---- Access control -------------------------------------------------------------------


async def test_non_member_cannot_generate_or_fetch_overview(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    owner_tokens = await _signup(client, "ov-owner@test.com", "OvAccess")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "ov-member@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    org_id = await _org_id(client, owner_headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, owner_headers, org_id
    )

    gen_resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=member_headers)
    assert gen_resp.status_code == 403

    get_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=member_headers)
    assert get_resp.status_code == 403


async def test_shared_member_can_generate_and_fetch_overview(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any notebook member (creator OR a share recipient) may generate/fetch — not
    admin-only, matching the same access level chat already uses for this notebook."""
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    owner_tokens = await _signup(client, "ov-owner2@test.com", "OvShared")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    member_tokens = await _invite(client, owner_headers, "ov-member2@test.com", ROLE_MEMBER)
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_id = (await client.get("/auth/me", headers=member_headers)).json()["id"]

    org_id = await _org_id(client, owner_headers)
    notebook_id, _section_id = await _make_notebook_with_summary(
        client, session_factory, owner_headers, org_id
    )
    share_resp = await client.post(
        f"/notebooks/{notebook_id}/shares", headers=owner_headers, json={"user_id": member_id}
    )
    assert share_resp.status_code == 204

    app.dependency_overrides[get_llm] = lambda: _OverviewLLM()
    gen_resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=member_headers)
    assert gen_resp.status_code == 200

    get_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=member_headers)
    assert get_resp.status_code == 200


async def test_cross_org_notebook_404_on_overview_endpoints(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NOTEBOOK_OVERVIEW_ENABLED", True)
    tokens_a = await _signup(client, "ov-isoa@test.com", "OvIsoA")
    tokens_b = await _signup(client, "ov-isob@test.com", "OvIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (
        await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    ).json()["id"]

    gen_resp = await client.post(f"/notebooks/{notebook_id}/overview", headers=headers_b)
    assert gen_resp.status_code == 404

    get_resp = await client.get(f"/notebooks/{notebook_id}/overview", headers=headers_b)
    assert get_resp.status_code == 404


# ---- Org scoping on the repository -----------------------------------------------------


async def test_notebook_overview_repository_org_scoped(session_factory, tenant_engine) -> None:
    """Repository-level backstop, independent of the HTTP-level checks above."""
    from app.services.knowledge.overview import NotebookOverviewRepository

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    notebook_b = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_a, name="OrgA"))
        session.add(Organization(id=org_b, name="OrgB"))
        await session.flush()
        session.add(Notebook(id=notebook_b, org_id=org_b, name="Secret NB", created_by=None))
        await session.flush()
        session.add(
            NotebookOverview(
                org_id=org_b,
                notebook_id=notebook_b,
                content="secret overview",
                citations=[],
                generated_at=datetime.now(UTC),
                generated_by=None,
                source_document_count=1,
                stale=False,
            )
        )

    async with session_factory() as session:
        row_a = await NotebookOverviewRepository(
            session, TenantContext(org_id=org_a)
        ).get_by_notebook(notebook_b)
        assert row_a is None

        row_b = await NotebookOverviewRepository(
            session, TenantContext(org_id=org_b)
        ).get_by_notebook(notebook_b)
        assert row_b is not None
        assert row_b.content == "secret overview"
