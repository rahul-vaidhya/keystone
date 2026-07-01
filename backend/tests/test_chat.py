"""F40 grounded generation: context wiring into the prompt, citation carry-through, org
isolation (inherited from F31), and the LLM-seam-failure retry/clean-error path.

Uses the FAKE LLM/embedder seams exclusively — deterministic, no network, no API keys.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.chat import Conversation
from app.models.chat import Message as MessageRow
from app.models.documents import Document
from app.models.ingestion import Chunk, Embedding
from app.platform import config
from app.platform.seams import EMBED_DIM, Message, SeamTransientError, get_llm
from main import app

FAKE_MODEL = f"fake-embed-{EMBED_DIM}"


class _AlwaysFailingLLM:
    """Raises SeamTransientError on every call — exercises the retry-then-clean-failure
    path without touching a real vendor."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model(self) -> str:
        return "always-failing"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.calls += 1
        raise SeamTransientError("simulated transient failure")
        yield ""  # pragma: no cover - unreachable, satisfies the async-generator shape


class _BuggyLLM:
    """Raises a non-transient error (a bug, not a vendor hiccup) — must propagate
    immediately, NOT be retried, NOT be buried behind a 503."""

    @property
    def model(self) -> str:
        return "buggy"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        raise ValueError("not a seam error")
        yield ""  # pragma: no cover - unreachable


class _MultiCitingLLM:
    """Cites every numbered block it was sent, e.g. ``[1] [2]`` — exercises
    multi-citation resolution, which the default ``FakeLLM`` (always exactly ``[1]``)
    cannot."""

    @property
    def model(self) -> str:
        return "multi-citing"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        user_content = next((m.content for m in reversed(messages) if m.role == "user"), "")
        markers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", user_content)})
        answer = "Answer citing " + " ".join(f"[{n}]" for n in markers)
        for token in answer.split(" "):
            yield token + " "


class _OutOfRangeCitingLLM:
    """Cites a marker that was never sent — exercises the drop-invalid-marker path."""

    @property
    def model(self) -> str:
        return "out-of-range-citing"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        for token in "This cites a source that does not exist [99]".split(" "):
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


async def _org_id(client: AsyncClient, headers: dict) -> uuid.UUID:
    me = await client.get("/auth/me", headers=headers)
    return uuid.UUID(me.json()["org_id"])


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


async def test_ask_grounded_answer_cites_retrieved_context(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chat-ground@test.com", "Ground")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "[1]" in body["answer"]
    assert body["answer"] != "I don't have that in the provided sources."
    assert len(body["citations"]) == 1
    assert body["citations"][0]["marker"] == 1
    assert body["citations"][0]["document_id"] == str(doc_id)
    assert body["citations"][0]["content"] == "alpha content about onboarding"
    assert body["citations"][0]["char_start"] == 0
    assert body["citations"][0]["char_end"] == len("alpha content about onboarding")
    assert body["model"] == "fake-llm"
    assert body["correlation_id"]
    assert body["conversation_id"]
    assert body["message_id"]


async def test_ask_refuses_when_notebook_has_no_context(client: AsyncClient) -> None:
    tokens = await _signup(client, "chat-empty@test.com", "Empty")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "anything"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "I don't have that in the provided sources."
    assert body["citations"] == []


async def test_ask_cross_org_notebook_404s(client: AsyncClient) -> None:
    tokens_a = await _signup(client, "chat-isoa@test.com", "IsoA")
    tokens_b = await _signup(client, "chat-isob@test.com", "IsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (
        await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    ).json()["id"]

    resp = await client.post(
        "/chat/ask", headers=headers_b, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 404


async def test_ask_multiple_chunks_numbered_citations_match_blocks(
    client: AsyncClient, session_factory
) -> None:
    """Uses ``_MultiCitingLLM`` (not the default ``FakeLLM``, which only ever cites
    ``[1]``) so this genuinely exercises resolving more than one marker — citations come
    back ordered by marker, one per ``[n]`` the model actually cited, not "every block
    retrieval happened to return"."""
    tokens = await _signup(client, "chat-multi@test.com", "Multi")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=0, content="first", seed=1
    )
    await _seed_chunk_with_embedding(
        session_factory, org_id=org_id, document_id=doc_id, ordinal=1, content="second", seed=2
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    app.dependency_overrides[get_llm] = lambda: _MultiCitingLLM()
    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "q", "k": 8}
    )
    body = resp.json()
    markers = [c["marker"] for c in body["citations"]]
    assert markers == [1, 2]


async def test_ask_citation_provenance_round_trip_matches_stored_chunk(
    client: AsyncClient, session_factory
) -> None:
    """The F41 behavioral gate: a resolved citation's char span/content must match the
    SAME chunk row an independent direct DB read returns — not just whatever
    ``ContextBlock`` self-reported during retrieval."""
    tokens = await _signup(client, "chat-prov@test.com", "Prov")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "provenance content span"
    )

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "provenance"}
    )
    body = resp.json()
    citation = body["citations"][0]

    async with session_factory() as session:
        stored_chunk = (
            await session.execute(select(Chunk).where(Chunk.id == uuid.UUID(citation["chunk_id"])))
        ).scalar_one()

    assert citation["document_id"] == str(stored_chunk.document_id)
    assert citation["char_start"] == stored_chunk.char_start
    assert citation["char_end"] == stored_chunk.char_end
    assert citation["content"] == stored_chunk.content
    span = stored_chunk.content[citation["char_start"] : citation["char_end"]]
    assert span == citation["content"]


async def test_ask_out_of_range_marker_is_dropped_not_fabricated(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chat-oor@test.com", "OOR")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "only one chunk here"
    )

    app.dependency_overrides[get_llm] = lambda: _OutOfRangeCitingLLM()
    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    body = resp.json()
    assert "[99]" in body["answer"]  # the malformed reference survives in the answer text
    assert body["citations"] == []  # but is never resolved into a fabricated citation


async def test_ask_persists_conversation_and_message_with_citations(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chat-persist@test.com", "Persist")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "persisted content"
    )

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "persisted"}
    )
    body = resp.json()

    async with session_factory() as session:
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.id == uuid.UUID(body["conversation_id"]))
            )
        ).scalar_one()
        assert conversation.org_id == org_id
        assert str(conversation.knowledge_base_id) == notebook_id

        messages = (
            (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.conversation_id == conversation.id)
                    .order_by(MessageRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "persisted"
    assert messages[0].citations is None
    assistant_message = messages[1]
    assert str(assistant_message.id) == body["message_id"]
    assert assistant_message.content == body["answer"]
    assert assistant_message.citations == body["citations"]


async def test_ask_llm_transient_failure_retries_then_returns_clean_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "LLM_RETRY_BACKOFF_BASE_SECONDS", 0.01)
    tokens = await _signup(client, "chat-fail@test.com", "Fail")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB2"})).json()[
        "id"
    ]

    failing_llm = _AlwaysFailingLLM()
    app.dependency_overrides[get_llm] = lambda: failing_llm

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 503
    assert failing_llm.calls > 1  # retried, not just one attempt


async def test_ask_llm_non_transient_error_propagates_without_retry(
    client: AsyncClient,
) -> None:
    tokens = await _signup(client, "chat-bug@test.com", "Bug")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]

    app.dependency_overrides[get_llm] = lambda: _BuggyLLM()

    # ASGITransport re-raises unhandled exceptions rather than turning them into an HTTP
    # response — which is exactly the point: a non-transient error is NOT caught/retried/
    # turned into a clean GenerationFailed/503, it propagates as itself.
    with pytest.raises(ValueError, match="not a seam error"):
        await client.post(
            "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
        )


def test_build_messages_includes_grounding_instruction_and_numbered_context() -> None:
    from app.schemas.retrieval import ContextBlock
    from app.services.chat import build_messages

    block = ContextBlock(
        index=1,
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        char_start=0,
        char_end=5,
        content="hello",
        distance=0.1,
    )
    messages = build_messages("what is this?", [block])
    assert messages[0].role == "system"
    assert "I don't have that in the provided sources." in messages[0].content
    assert messages[1].role == "user"
    assert "[1] hello" in messages[1].content
    assert "Question: what is this?" in messages[1].content


def test_build_messages_empty_context_has_no_numbered_block() -> None:
    from app.services.chat import build_messages

    messages = build_messages("q", [])
    assert "[1]" not in messages[1].content
