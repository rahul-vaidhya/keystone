"""Per-sentence citation checker (CITATION_CHECK_ENABLED): pure splitter/scoring unit
tests (no DB), plus /chat/ask + /chat/stream + history integration on the fake seams.

FakeEmbedder is hash-based (semantically meaningless), so status assertions in the
integration tests never depend on semantic meaning — the unit tests drive scoring with
explicit lexical functions / vector dicts instead.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.settings import settings
from app.models.chat import Message as MessageRow
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding
from app.models.retrieval import ContextBlock
from app.services.chat.citation_check import (
    check_claims,
    is_checkable,
    score_claims,
    sentence_markers,
    split_sentences,
    strip_markers,
)
from app.services.retrieval.sparse import InvertedIndex, SparseDoc, tfidf_cosine
from app.services.seams import EMBED_DIM, Message, get_llm
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"

OCTET = "The octet rule says atoms gain or lose electrons to reach eight valence electrons."
FAJANS = "Fajans rules describe the covalent character of ionic bonds and polarisation."


def _block(index: int, content: str) -> ContextBlock:
    return ContextBlock(
        index=index,
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        char_start=0,
        char_end=len(content),
        content=content,
        distance=0.1,
    )


def _index() -> InvertedIndex:
    return InvertedIndex.build(
        [
            SparseDoc(doc_id="a", zones={"heading": "", "body": OCTET}),
            SparseDoc(doc_id="b", zones={"heading": "", "body": FAJANS}),
            SparseDoc(doc_id="c", zones={"heading": "", "body": "Bananas are a tropical fruit."}),
        ]
    )


# ---- pure unit tests ----


def test_split_sentences_keeps_markers_including_after_full_stop() -> None:
    answer = (
        "Atoms want eight electrons [1]. Ionic bonds can be covalent. [2]\nSee also this point!"
    )
    sentences = split_sentences(answer)
    assert sentences == [
        "Atoms want eight electrons [1].",
        "Ionic bonds can be covalent. [2]",
        "See also this point!",
    ]
    assert sentence_markers("Both [2] and [1][2] apply.") == [2, 1]
    assert strip_markers("Atoms want eight electrons [1].") == "Atoms want eight electrons ."


def test_split_sentences_trailing_marker_tail_joins_previous_sentence() -> None:
    assert split_sentences("Ionic bonds can be covalent. [2][3]") == [
        "Ionic bonds can be covalent. [2][3]"
    ]


def test_is_checkable_skips_short_and_fixed_messages() -> None:
    assert not is_checkable("Yes [1].")
    assert not is_checkable("I don't have that in the provided sources.")
    assert not is_checkable("The available sources don't contain a strong match for this question.")
    assert is_checkable("Atoms want eight valence electrons [1].")


def test_supported_vs_weak_vs_uncited_lexical_only() -> None:
    index = _index()
    blocks = [_block(1, OCTET), _block(2, FAJANS)]
    sentences = [
        "Atoms gain or lose electrons to reach eight valence electrons [1].",
        "Bananas are a tropical fruit grown widely [2].",  # unrelated to its cited chunk
        "This sentence makes a claim without any citation.",
    ]
    checks = score_claims(
        sentences,
        blocks,
        lexical_fn=lambda a, b: tfidf_cosine(index, a, b),
        vectors=None,
        threshold=0.3,
    )
    assert [c.status for c in checks] == ["supported", "weak", "uncited"]
    assert checks[0].citations == [1] and checks[0].lexical > 0.3
    assert checks[0].semantic is None and checks[0].score == checks[0].lexical
    assert checks[1].lexical == 0.0 and checks[1].score == 0.0
    assert checks[2].citations == [] and checks[2].score is None


def test_combined_score_is_mean_of_lexical_and_semantic() -> None:
    blocks = [_block(1, OCTET)]
    sentence = "Atoms like eight electrons in their shell [1]."
    text = strip_markers(sentence)
    vectors = {text: [1.0, 0.0], OCTET: [1.0, 0.0]}  # semantic cosine = 1.0
    checks = score_claims(
        [sentence], blocks, lexical_fn=lambda a, b: 0.2, vectors=vectors, threshold=0.5
    )
    assert checks[0].lexical == 0.2
    assert checks[0].semantic == 1.0
    assert checks[0].score == pytest.approx(0.6)
    assert checks[0].status == "supported"


def test_out_of_range_marker_is_weak_with_no_scores() -> None:
    checks = score_claims(
        ["This cites a source that was never sent [9]."],
        [_block(1, OCTET)],
        lexical_fn=lambda a, b: 1.0,
        vectors=None,
        threshold=0.3,
    )
    assert checks[0].status == "weak" and checks[0].score is None


class _CountingEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    @property
    def model(self) -> str:
        return "counting"

    @property
    def dim(self) -> int:
        return 2

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedder down")
        return [[1.0, float(len(t) % 3)] for t in texts]


async def test_check_claims_uses_one_batched_embed_call() -> None:
    embedder = _CountingEmbedder()
    answer = "Atoms want eight valence electrons [1]. Ionic bonds polarise strongly [2]."
    checks = await check_claims(
        answer,
        [_block(1, OCTET), _block(2, FAJANS)],
        index=_index(),
        embedder=embedder,
        correlation_id="t",
    )
    assert embedder.calls == 1
    assert len(checks) == 2
    assert all(c.semantic is not None and c.lexical is not None for c in checks)


async def test_check_claims_embedder_failure_degrades_to_lexical_only() -> None:
    checks = await check_claims(
        "Atoms gain or lose electrons to reach eight valence electrons [1].",
        [_block(1, OCTET)],
        index=_index(),
        embedder=_CountingEmbedder(fail=True),
        correlation_id="t",
    )
    assert checks[0].semantic is None
    assert checks[0].lexical is not None and checks[0].score == checks[0].lexical


async def test_check_claims_without_index_is_semantic_only() -> None:
    checks = await check_claims(
        "Atoms want eight valence electrons [1].",
        [_block(1, OCTET)],
        index=None,
        embedder=_CountingEmbedder(),
        correlation_id="t",
    )
    assert checks[0].lexical is None and checks[0].semantic is not None


# ---- integration (fake seams, real Postgres) ----


class _TwoSentenceLLM:
    """One cited sentence + one uncited sentence (when context is present)."""

    @property
    def model(self) -> str:
        return "two-sentence"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        answer = (
            "Atoms gain or lose electrons to reach eight valence electrons [1]. "
            "This extra sentence makes a claim with no citation at all."
        )
        for token in answer.split(" "):
            yield token + " "


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_llm, None)


async def _setup(client: AsyncClient, session_factory, email: str) -> tuple[dict, str]:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": email}
    )
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    org_id = uuid.UUID((await client.get("/auth/me", headers=headers)).json()["org_id"])
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Document(id=doc_id, org_id=org_id, title="Doc"))
        await session.flush()
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=doc_id,
                section_id=None,
                ordinal=0,
                content=OCTET,
                token_count=20,
                char_start=0,
                char_end=len(OCTET),
            )
        )
        # A second, unrelated chunk with NO embedding: never retrieved by kNN (so the
        # answer's [1] is always the octet chunk), but it is in the sparse index, giving
        # the corpus non-zero idf (a 1-document corpus has idf = log(1/1) = 0 for all).
        session.add(
            Chunk(
                id=uuid.uuid4(),
                org_id=org_id,
                document_id=doc_id,
                section_id=None,
                ordinal=1,
                content="Bananas are a tropical fruit.",
                token_count=8,
                char_start=len(OCTET),
                char_end=len(OCTET) + 29,
            )
        )
        vec = [0.0] * EMBED_DIM
        vec[0] = 1.0
        session.add(
            Embedding(
                org_id=org_id,
                document_id=doc_id,
                owner_type="chunk",
                owner_id=chunk_id,
                model=FAKE_MODEL,
                dim=EMBED_DIM,
                embedding=vec,
            )
        )
    nb = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = nb.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
    return headers, notebook_id


async def test_flag_on_ask_returns_persists_and_hydrates_claim_checks(
    client: AsyncClient, session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "CITATION_CHECK_ENABLED", True)
    app.dependency_overrides[get_llm] = lambda: _TwoSentenceLLM()
    headers, notebook_id = await _setup(client, session_factory, "citecheck-ask@test.com")

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "octet"}
    )
    assert resp.status_code == 200
    checks = resp.json()["claim_checks"]
    assert [c["status"] for c in checks][1] == "uncited"
    assert checks[0]["citations"] == [1]
    # The cited sentence paraphrases its chunk: lexical support is real (sparse index
    # over the notebook's documents), semantic is a number (fake embedder, meaningless).
    assert checks[0]["lexical"] > 0.3
    assert checks[0]["semantic"] is not None
    assert checks[0]["status"] in {"supported", "weak"}

    message_id = uuid.UUID(resp.json()["message_id"])
    async with session_factory() as session:
        row = (
            await session.execute(select(MessageRow).where(MessageRow.id == message_id))
        ).scalar_one()
    assert row.claim_checks == checks

    history = await client.get(f"/chat/notebooks/{notebook_id}/messages", headers=headers)
    assistant = next(m for m in history.json() if m["role"] == "assistant")
    user = next(m for m in history.json() if m["role"] == "user")
    assert assistant["claim_checks"] == checks
    assert user["claim_checks"] is None


async def test_flag_off_ask_has_no_claim_checks(client: AsyncClient, session_factory) -> None:
    assert settings.CITATION_CHECK_ENABLED is False
    app.dependency_overrides[get_llm] = lambda: _TwoSentenceLLM()
    headers, notebook_id = await _setup(client, session_factory, "citecheck-off@test.com")

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "octet"}
    )
    assert resp.status_code == 200
    assert resp.json()["claim_checks"] is None
    message_id = uuid.UUID(resp.json()["message_id"])
    async with session_factory() as session:
        row = (
            await session.execute(select(MessageRow).where(MessageRow.id == message_id))
        ).scalar_one()
    assert row.claim_checks is None

    stream = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "octet"}
    )
    done = next(e for e in _sse(stream.text) if e["type"] == "done")
    assert "claim_checks" not in done


def _sse(text: str) -> list[dict]:
    return [
        json.loads(block.strip()[6:])
        for block in text.split("\n\n")
        if block.strip().startswith("data: ")
    ]


async def test_flag_on_stream_done_event_carries_claim_checks(
    client: AsyncClient, session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "CITATION_CHECK_ENABLED", True)
    app.dependency_overrides[get_llm] = lambda: _TwoSentenceLLM()
    headers, notebook_id = await _setup(client, session_factory, "citecheck-stream@test.com")

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "octet"}
    )
    assert resp.status_code == 200
    done = next(e for e in _sse(resp.text) if e["type"] == "done")
    assert len(done["claim_checks"]) == 2
    assert done["claim_checks"][0]["citations"] == [1]
    assert done["claim_checks"][1]["status"] == "uncited"

    async with session_factory() as session:
        row = (
            await session.execute(
                select(MessageRow).where(MessageRow.id == uuid.UUID(done["message_id"]))
            )
        ).scalar_one()
    assert row.claim_checks == done["claim_checks"]
