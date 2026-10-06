"""Integration tests for ``POST /retrieval/sparse-search`` — the from-scratch IR core
made visible: ranked (tf-idf / BM25) with explanations, Boolean with merge steps, phrase
with positional verification, plus notebook scoping, cross-org 404 and private-notebook
403."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.documents import Document
from app.models.ingestion import Chunk, Section
from app.services.retrieval import sparse_channel
from app.utils.constants import ROLE_MEMBER
from main import app

CORPUS = [
    ("Ionic Bonds", "### Page 3\nThe octet rule explains ionic bonding in sodium chloride."),
    ("Covalent Bonds", "A covalent bond shares electrons so the octet rule holds."),
    ("Hydrogen Bonding", "A hydrogen bond is weaker than a covalent bond."),
    ("Lattice", "Lattice enthalpy measures the ionic lattice energy of a crystal."),
    ("Gases", "Hydrogen gas is diatomic; the hydrogen bond here is not covalent."),
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    sparse_channel.clear_cache()
    yield
    sparse_channel.clear_cache()


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _signup(client: AsyncClient, email: str, org: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org}
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _seed_notebook(client, session_factory, headers, *, title="chem.pdf") -> str:
    me = await client.get("/auth/me", headers=headers)
    org_id = uuid.UUID(me.json()["org_id"])
    doc_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title=title))
    for i, (heading, body) in enumerate(CORPUS):
        section_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            session.add(
                Section(
                    id=section_id,
                    org_id=org_id,
                    document_id=doc_id,
                    parent_section_id=None,
                    ordinal=i,
                    depth=1,
                    path=str(i + 1),
                    heading=heading,
                    page_start=i + 1,
                    page_end=i + 1,
                    char_start=i * 1000,
                    char_end=i * 1000 + len(body),
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    document_id=doc_id,
                    section_id=section_id,
                    ordinal=i,
                    content=body,
                    token_count=max(len(body) // 4, 1),
                    char_start=i * 1000,
                    char_end=i * 1000 + len(body),
                )
            )
    nb = await client.post("/notebooks", headers=headers, json={"name": "IR"})
    notebook_id = nb.json()["id"]
    attach = await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    assert attach.status_code in (200, 201, 204)
    return notebook_id


async def _search(client, headers, notebook_id, **body):
    return await client.post(
        "/retrieval/sparse-search", headers=headers, json={"notebook_id": notebook_id, **body}
    )


@pytest.mark.parametrize("scheme", ["tfidf", "bm25"])
async def test_ranked_returns_explained_hits(client, session_factory, scheme) -> None:
    headers = await _signup(client, f"sparsesearch-ranked-{scheme}@test.com", "SparseRanked")
    nb = await _seed_notebook(client, session_factory, headers)

    resp = await _search(client, headers, nb, query="The octet rules", mode="ranked", scheme=scheme)
    assert resp.status_code == 200
    body = resp.json()
    analysis = body["analysis"]
    assert analysis["raw_tokens"] == ["The", "octet", "rules"]
    assert analysis["stop_words_removed"] == ["the"]
    assert analysis["stems"] == ["octet", "rule"]
    assert {t["term"]: t["df"] for t in analysis["terms"]} == {"octet": 2, "rule": 2}
    assert analysis["ranked"]["scheme"] == scheme

    stats = body["index_stats"]
    assert stats["n_docs"] == 5 and stats["vocabulary_size"] > 10
    assert stats["cached"] is False and stats["zones"] == ["body", "heading"]

    results = body["results"]
    assert len(results) == 2 and body["total_matches"] == 2
    for r in results:
        assert sum(c["weight"] for c in r["contributions"]) == pytest.approx(r["score"])
        assert r["document_title"] == "chem.pdf"
        assert {"octet", "rule"} <= set(r["matched_terms"])
        assert "octet" in r["highlights"]
    assert results[0]["score"] >= results[1]["score"]
    # page derived exactly as for chat citations (the "### Page 3" marker)
    ionic = next(r for r in results if r["heading"] == "Ionic Bonds")
    assert ionic["page_start"] == 3

    again = await _search(client, headers, nb, query="octet", mode="ranked")
    assert again.json()["index_stats"]["cached"] is True


async def test_ranked_index_elimination_and_zone_weights(client, session_factory) -> None:
    headers = await _signup(client, "sparsesearch-elim@test.com", "SparseElim")
    nb = await _seed_notebook(client, session_factory, headers)

    resp = await _search(client, headers, nb, query="bond lattice", idf_threshold=0.3)
    terms = {t["term"]: t for t in resp.json()["analysis"]["terms"]}
    assert terms["bond"]["eliminated"] is True  # df 4 of 5 -> idf 0.097
    assert terms["lattic"]["eliminated"] is False
    results = resp.json()["results"]
    assert [r["heading"] for r in results] == ["Lattice"]

    # body weight 0 -> only the heading zone scores
    heading_only = await _search(
        client, headers, nb, query="hydrogen", zone_weights={"heading": 1.0, "body": 0.0}
    )
    hits = heading_only.json()["results"]
    assert [r["heading"] for r in hits] == ["Hydrogen Bonding"]
    assert all(c["zone"] == "heading" for c in hits[0]["contributions"])


async def test_boolean_mode_shows_merge_order(client, session_factory) -> None:
    headers = await _signup(client, "sparsesearch-bool@test.com", "SparseBool")
    nb = await _seed_notebook(client, session_factory, headers)

    resp = await _search(client, headers, nb, query="hydrogen AND bond NOT lattice", mode="boolean")
    assert resp.status_code == 200
    body = resp.json()
    trace = body["analysis"]["boolean"]
    assert trace["operators"] == ["AND", "NOT"]
    steps = trace["clauses"][0]["steps"]
    assert [s["op"] for s in steps] == ["START", "AND", "AND NOT"]
    assert steps[0]["term"] == "hydrogen" and steps[0]["df"] <= steps[1]["df"]
    headings = sorted(r["heading"] for r in body["results"])
    assert headings == ["Gases", "Hydrogen Bonding"]
    assert body["total_matches"] == 2
    for r in body["results"]:
        assert r["score"] is None and set(r["matched_terms"]) == {"hydrogen", "bond"}


async def test_phrase_mode_reports_positional_check(client, session_factory) -> None:
    headers = await _signup(client, "sparsesearch-phrase@test.com", "SparsePhrase")
    nb = await _seed_notebook(client, session_factory, headers)

    resp = await _search(client, headers, nb, query='"covalent bond"', mode="phrase")
    body = resp.json()
    trace = body["analysis"]["phrase"]
    assert trace["phrase"] == "covalent bond"
    assert [t["term"] for t in trace["terms"]] == ["coval", "bond"]
    assert trace["candidates"] >= trace["matched"] == 2
    assert body["analysis"]["raw_tokens"] == ["covalent", "bond"]
    headings = sorted(r["heading"] for r in body["results"])
    assert headings == ["Covalent Bonds", "Hydrogen Bonding"]
    for r in body["results"]:
        assert r["phrase_matches"] and r["phrase_matches"][0]["zone"] == "body"


async def test_cross_org_notebook_404s(client, session_factory) -> None:
    headers_a = await _signup(client, "sparsesearch-isoa@test.com", "SparseIsoA")
    headers_b = await _signup(client, "sparsesearch-isob@test.com", "SparseIsoB")
    nb = await _seed_notebook(client, session_factory, headers_a)
    resp = await _search(client, headers_b, nb, query="octet")
    assert resp.status_code == 404


async def test_private_notebook_of_another_user_403s(client, session_factory) -> None:
    owner = await _signup(client, "sparsesearch-owner@test.com", "SparsePriv")
    invite = await client.post(
        "/auth/invite",
        headers=owner,
        json={"email": "sparsesearch-member@test.com", "role": ROLE_MEMBER},
    )
    inv = invite.json()
    accept = await client.post(
        "/auth/accept-invite",
        json={"org_id": inv["org_id"], "token": inv["invite_token"], "password": "password123"},
    )
    member = {"Authorization": f"Bearer {accept.json()['access_token']}"}
    nb = await _seed_notebook(client, session_factory, owner)
    resp = await _search(client, member, nb, query="octet", mode="boolean")
    assert resp.status_code == 403


async def test_validation_bounds(client, session_factory) -> None:
    headers = await _signup(client, "sparsesearch-valid@test.com", "SparseValid")
    nb = await _seed_notebook(client, session_factory, headers)
    assert (await _search(client, headers, nb, query="x", k=0)).status_code == 422
    assert (await _search(client, headers, nb, query="x", mode="fuzzy")).status_code == 422
    empty = await _search(client, headers, nb, query="the of", mode="ranked")
    assert empty.status_code == 200 and empty.json()["results"] == []
