"""F40 grounded generation: context wiring into the prompt, citation carry-through, org
isolation (inherited from F31), and the LLM-seam-failure retry/clean-error path.

Uses the FAKE LLM/embedder seams exclusively — deterministic, no network, no API keys.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models.chat import Conversation, MessageTrace
from app.models.chat import Message as MessageRow
from app.models.documents import Document
from app.models.ingestion import Chunk, ChunkHit, Embedding, Section
from app.services.seams import EMBED_DIM, Message, SeamTransientError, get_llm, get_reranker
from app.utils.constants import ROLE_MEMBER
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


async def _seed_chunk_with_section_and_embedding(
    session_factory,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    ordinal: int,
    content: str,
    page_start: int,
    page_end: int,
    seed: int = 0,
) -> uuid.UUID:
    """Same as ``_seed_chunk_with_embedding`` but the chunk carries a real
    ``section_id`` FK to a seeded ``Section`` row with the given page range — exercises
    the page-number citation join, distinct from the no-section case."""
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
                heading="Test Section",
                page_start=page_start,
                page_end=page_end,
                char_start=0,
                char_end=len(content),
            )
        )
        await session.flush()  # Section must be inserted before the Chunk FK references it
        session.add(
            Chunk(
                id=chunk_id,
                org_id=org_id,
                document_id=document_id,
                section_id=section_id,
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


async def test_ask_citation_includes_page_range_from_section(
    client: AsyncClient, session_factory
) -> None:
    """A citation for a chunk WITH a section reference carries page_start/page_end
    matching the section's own values — the human-readable-citation fix — and those
    values are independently org-scoped (the join predicate filters Section.org_id too,
    not just the FK)."""
    tokens = await _signup(client, "chat-page@test.com", "Page")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    doc_id = await _seed_document(session_factory, org_id, "Doc")
    await _seed_chunk_with_section_and_embedding(
        session_factory,
        org_id=org_id,
        document_id=doc_id,
        ordinal=0,
        content="paginated content span",
        page_start=3,
        page_end=4,
    )
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]
    await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "paginated"}
    )
    assert resp.status_code == 200
    body = resp.json()
    citation = body["citations"][0]
    assert citation["page_start"] == 3
    assert citation["page_end"] == 4


async def test_ask_citation_page_range_none_without_section(
    client: AsyncClient, session_factory
) -> None:
    """A citation for a chunk with ``section_id=None`` never fabricates a page number —
    both fields resolve to ``None``, and the request still succeeds (no crash)."""
    tokens = await _signup(client, "chat-nopage@test.com", "NoPage")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "no section content here"
    )

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "section"}
    )
    assert resp.status_code == 200
    body = resp.json()
    citation = body["citations"][0]
    assert citation["page_start"] is None
    assert citation["page_end"] is None


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
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_BASE_SECONDS", 0.01)
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
    from app.models.retrieval import ContextBlock
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


# ---- SSE streaming tests (F4x POST /chat/stream) ----


def _parse_sse_events(response_text: str) -> list[dict]:
    """Parse SSE event stream: split on double newline, strip 'data: ' prefix, json.loads each."""
    import json

    events = []
    for block in response_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("data: "):
            events.append(json.loads(block[6:]))
    return events


async def test_stream_grounded_answer_yields_tokens_and_done_event(
    client: AsyncClient, session_factory
) -> None:
    """Happy path: streaming a notebook with attached document yields one or more token
    events followed by a done event. Tokens concatenate to the full answer. Done event
    carries conversation_id, message_id, and citations."""
    tokens = await _signup(client, "chatstream-ground@test.com", "StreamGround")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "streaming content about topics"
    )

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "streaming"}
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    assert len(events) > 0

    # Separate token events from done event
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]

    # Must have at least one token and exactly one done
    assert len(token_events) >= 1
    assert len(done_events) == 1

    # Concatenated tokens should form the answer
    concatenated_answer = "".join(e["content"] for e in token_events).strip()
    assert len(concatenated_answer) > 0
    assert "[1]" in concatenated_answer  # FakeLLM cites when context is present

    # Done event carries the full answer and metadata
    done = done_events[0]
    assert done["type"] == "done"
    assert done["answer"] == concatenated_answer
    assert done["conversation_id"]
    assert done["message_id"]
    assert done["notebook_id"] == notebook_id
    assert done["query"] == "streaming"
    assert done["model"] == "fake-llm"
    assert done["correlation_id"]
    assert isinstance(done["citations"], list)
    assert len(done["citations"]) == 1
    assert done["citations"][0]["marker"] == 1


async def test_stream_persists_conversation_and_message_with_citations(
    client: AsyncClient, session_factory
) -> None:
    """Persistence parity: after streaming completes, conversation + user message +
    assistant message rows exist in the DB with correct citations."""
    tokens = await _signup(client, "chatstream-persist@test.com", "StreamPersist")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "persisted streaming content"
    )

    resp = await client.post(
        "/chat/stream",
        headers=headers,
        json={"notebook_id": notebook_id, "query": "persisted"},
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    done = next(e for e in events if e["type"] == "done")

    # Verify DB rows exist
    async with session_factory() as session:
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.id == uuid.UUID(done["conversation_id"]))
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
    assert str(assistant_message.id) == done["message_id"]
    assert assistant_message.content == done["answer"]
    assert assistant_message.citations == done["citations"]


async def test_stream_refuses_when_notebook_has_no_context(client: AsyncClient) -> None:
    """Refusal path: a notebook with no attached documents streams the refusal answer
    and a done event with empty citations."""
    tokens = await _signup(client, "chatstream-empty@test.com", "StreamEmpty")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "anything"}
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    done_events = [e for e in events if e["type"] == "done"]

    assert len(done_events) == 1
    done = done_events[0]
    assert done["answer"] == "I don't have that in the provided sources."
    assert done["citations"] == []


async def test_stream_llm_failure_mid_stream_yields_error_event(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Error path: when the LLM fails mid-stream (after yielding at least one token),
    an error event is sent and HTTP status is still 200 (SSE cannot change status
    mid-stream). No done event is sent after error."""
    tokens = await _signup(client, "chatstream-fail@test.com", "StreamFail")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]

    class _MidStreamFailingLLM:
        """Yields one token then fails."""

        @property
        def model(self) -> str:
            return "mid-stream-failing"

        async def stream(self, messages: list) -> AsyncIterator[str]:
            yield "partial"
            raise SeamTransientError("simulated mid-stream failure")
            yield ""  # pragma: no cover - unreachable

    app.dependency_overrides[get_llm] = lambda: _MidStreamFailingLLM()

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    # HTTP status is 200 even though an error occurred mid-stream (SSE constraint)
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    token_events = [e for e in events if e["type"] == "token"]
    error_events = [e for e in events if e["type"] == "error"]
    done_events = [e for e in events if e["type"] == "done"]

    # At least one token was sent before the error
    assert len(token_events) >= 1
    # Error event exists
    assert len(error_events) >= 1
    # No done event after error
    assert len(done_events) == 0


async def test_stream_failure_log_includes_error_type_even_when_str_is_empty(
    client: AsyncClient,
) -> None:
    """Some real exceptions (e.g. httpx's ReadTimeout as actually raised by RealReranker)
    have an empty str() — `error=str(exc)` alone then logs a useless blank string. The
    stream-failure log must also carry `error_type` (the exception's class name) so a
    genuinely message-less failure is still diagnosable from the persisted log line."""
    tokens = await _signup(client, "chatstream-emptyerr@test.com", "StreamEmptyErr")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]

    class _EmptyMessageFailingLLM:
        """Raises an exception whose str() is the empty string, mirroring a real
        message-less httpx.ReadTimeout."""

        @property
        def model(self) -> str:
            return "empty-message-failing"

        async def stream(self, messages: list) -> AsyncIterator[str]:
            yield "partial"
            raise SeamTransientError()
            yield ""  # pragma: no cover - unreachable

    app.dependency_overrides[get_llm] = lambda: _EmptyMessageFailingLLM()

    with structlog.testing.capture_logs() as cap_logs:
        resp = await client.post(
            "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
        )
    assert resp.status_code == 200

    failure_logs = [e for e in cap_logs if e["event"] == "chat.stream_failed"]
    assert len(failure_logs) == 1
    assert failure_logs[0]["error"] == ""  # the empty-str() case this test targets
    assert failure_logs[0]["error_type"] == "SeamTransientError"


async def test_stream_missing_notebook_yields_error(client: AsyncClient) -> None:
    """Error path: a nonexistent notebook is rejected with a real 404 before the stream
    opens (known-issues S5: it used to be a 200 + generic "Stream failed" event)."""
    tokens = await _signup(client, "chatstream-missing@test.com", "StreamMissing")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    fake_notebook_id = str(uuid.uuid4())

    resp = await client.post(
        "/chat/stream",
        headers=headers,
        json={"notebook_id": fake_notebook_id, "query": "q"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Notebook not found"


async def test_stream_provider_failure_yields_actionable_error(client: AsyncClient) -> None:
    """A model-provider failure mid-stream (e.g. OpenRouter 401/402) tells the user to
    check the API key / credits instead of the bare "Stream failed"."""
    tokens = await _signup(client, "chatstream-provider@test.com", "StreamProvider")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    notebook_id = (await client.post("/notebooks", headers=headers, json={"name": "NB"})).json()[
        "id"
    ]

    class _ProviderError(Exception):
        status_code = 402

    class _RejectingLLM:
        @property
        def model(self) -> str:
            return "rejecting"

        async def stream(self, messages: list) -> AsyncIterator[str]:
            raise _ProviderError("Insufficient credits")
            yield ""  # pragma: no cover - unreachable

    app.dependency_overrides[get_llm] = lambda: _RejectingLLM()
    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 200
    (error,) = [e for e in _parse_sse_events(resp.text) if e["type"] == "error"]
    assert "API key / OpenRouter credits" in error["message"]


async def test_stream_citations_in_done_event_resolve_correctly(
    client: AsyncClient, session_factory
) -> None:
    """Citations in the done event must match the stored chunk (provenance round-trip)
    — the same guarantee as test_ask_citation_provenance_round_trip_matches_stored_chunk
    but for streaming."""
    tokens = await _signup(client, "chatstream-cit@test.com", "StreamCit")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "citation resolution content"
    )

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "citation"}
    )
    assert resp.status_code == 200

    events = _parse_sse_events(resp.text)
    done = next(e for e in events if e["type"] == "done")

    # Citations must be present in done event
    assert len(done["citations"]) > 0
    citation = done["citations"][0]

    # Verify the citation matches the stored chunk
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


async def test_stream_cross_org_notebook_yields_error(client: AsyncClient) -> None:
    """Cross-org isolation: another org's notebook is rejected with a 404 before the
    stream opens — same as ``/chat/ask`` (known-issues S5; it used to be 200 + an error
    event)."""
    tokens_a = await _signup(client, "chatstream-isoa@test.com", "StreamIsoA")
    tokens_b = await _signup(client, "chatstream-isob@test.com", "StreamIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}

    notebook_id = (
        await client.post("/notebooks", headers=headers_a, json={"name": "Secret"})
    ).json()["id"]

    resp = await client.post(
        "/chat/stream", headers=headers_b, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert resp.status_code == 404


# ---- F42 admin debug bundle (GET /chat/messages/{message_id}/trace) ----


async def _invite_member(client: AsyncClient, owner_headers: dict, email: str) -> dict:
    """Invites a member and accepts the invite (self-serve link flow — the invitee sets
    their own password), returning their own token dict."""
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


async def test_ask_persists_trace_with_hits_prompt_and_raw_output(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chattrace-ask@test.com", "Trace")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "trace content about onboarding"
    )

    resp = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "trace"}
    )
    body = resp.json()

    # Persisted directly (source of truth), not just via the endpoint.
    async with session_factory() as session:
        stored = (
            await session.execute(
                select(MessageTrace).where(MessageTrace.message_id == uuid.UUID(body["message_id"]))
            )
        ).scalar_one()
    assert stored.org_id == org_id
    assert stored.raw_output == body["answer"]
    assert len(stored.hits) == 1
    assert stored.hits[0]["document_id"] == str(doc_id)
    assert "trace" in stored.final_prompt
    assert "onboarding" in stored.final_prompt

    trace_resp = await client.get(f"/chat/messages/{body['message_id']}/trace", headers=headers)
    assert trace_resp.status_code == 200
    trace = trace_resp.json()
    assert trace["message_id"] == body["message_id"]
    assert trace["raw_output"] == body["answer"]
    assert len(trace["hits"]) == 1
    assert trace["hits"][0]["chunk_id"] == body["citations"][0]["chunk_id"]


async def test_stream_persists_trace(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "chattrace-stream@test.com", "TraceStream")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "streamed trace content"
    )

    resp = await client.post(
        "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "streamed"}
    )
    done = next(e for e in _parse_sse_events(resp.text) if e["type"] == "done")

    trace_resp = await client.get(f"/chat/messages/{done['message_id']}/trace", headers=headers)
    assert trace_resp.status_code == 200
    trace = trace_resp.json()
    assert trace["raw_output"] == done["answer"]
    assert len(trace["hits"]) == 1
    assert trace["hits"][0]["document_id"] == str(doc_id)


async def test_get_trace_requires_admin(client: AsyncClient, session_factory) -> None:
    owner_tokens = await _signup(client, "chattrace-owner@test.com", "TraceOwner")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, owner_headers, session_factory, org_id, "member-gated content"
    )
    ask = await client.post(
        "/chat/ask", headers=owner_headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]

    member_tokens = await _invite_member(client, owner_headers, "chattrace-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    resp = await client.get(f"/chat/messages/{message_id}/trace", headers=member_headers)
    assert resp.status_code == 403


async def test_get_trace_missing_message_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "chattrace-missing@test.com", "TraceMissing")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.get(f"/chat/messages/{uuid.uuid4()}/trace", headers=headers)
    assert resp.status_code == 404


# ---- Chat history hydration (GET /chat/notebooks/{notebook_id}/messages) ----


async def test_list_messages_returns_chronological_pairs_from_multiple_asks(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chathist-multi@test.com", "Hist")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "history content about onboarding"
    )

    first = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "history"}
    )
    second = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "history again"}
    )
    assert first.status_code == 200
    assert second.status_code == 200

    resp = await client.get(f"/chat/notebooks/{notebook_id}/messages", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 4
    assert [m["role"] for m in body] == ["user", "assistant", "user", "assistant"]
    assert body[0]["content"] == "history"
    assert body[0]["citations"] is None
    assert body[2]["content"] == "history again"

    first_body = first.json()
    second_body = second.json()
    assert body[1]["content"] == first_body["answer"]
    assert body[1]["citations"] == first_body["citations"]
    assert body[1]["id"] == first_body["message_id"]
    assert body[3]["content"] == second_body["answer"]
    assert body[3]["citations"] == second_body["citations"]
    assert body[3]["id"] == second_body["message_id"]

    # Chronological order: created_at strictly non-decreasing.
    created_ats = [m["created_at"] for m in body]
    assert created_ats == sorted(created_ats)


async def test_list_messages_empty_notebook_returns_empty_list_not_404(
    client: AsyncClient,
) -> None:
    tokens = await _signup(client, "chathist-empty@test.com", "HistEmpty")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = await client.post("/notebooks", headers=headers, json={"name": "NB"})
    notebook_id = created.json()["id"]

    resp = await client.get(f"/chat/notebooks/{notebook_id}/messages", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_messages_nonexistent_notebook_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "chathist-missing@test.com", "HistMissing")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.get(f"/chat/notebooks/{uuid.uuid4()}/messages", headers=headers)
    assert resp.status_code == 404


async def test_list_messages_cross_org_notebook_404s(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "chathist-isoa@test.com", "HistIsoA")
    tokens_b = await _signup(client, "chathist-isob@test.com", "HistIsoB")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    org_id_a = await _org_id(client, headers_a)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers_a, session_factory, org_id_a, "org A history content"
    )
    ask = await client.post(
        "/chat/ask", headers=headers_a, json={"notebook_id": notebook_id, "query": "q"}
    )
    assert ask.status_code == 200

    resp = await client.get(f"/chat/notebooks/{notebook_id}/messages", headers=headers_b)
    assert resp.status_code == 404


async def test_list_messages_excludes_other_notebooks_in_same_org(
    client: AsyncClient, session_factory
) -> None:
    tokens = await _signup(client, "chathist-scope@test.com", "HistScope")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_a, _doc_a = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "notebook A content"
    )
    notebook_b, _doc_b = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "notebook B content"
    )

    await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_a, "query": "about A"}
    )
    await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_b, "query": "about B"}
    )

    resp_a = await client.get(f"/chat/notebooks/{notebook_a}/messages", headers=headers)
    resp_b = await client.get(f"/chat/notebooks/{notebook_b}/messages", headers=headers)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    body_a = resp_a.json()
    body_b = resp_b.json()
    assert len(body_a) == 2
    assert len(body_b) == 2
    assert body_a[0]["content"] == "about A"
    assert body_b[0]["content"] == "about B"
    # No cross-contamination: the ids are disjoint.
    ids_a = {m["id"] for m in body_a}
    ids_b = {m["id"] for m in body_b}
    assert ids_a.isdisjoint(ids_b)


async def test_get_trace_cross_org_404s(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "chattrace-isoa@test.com", "TraceIsoA")
    tokens_b = await _signup(client, "chattrace-isob@test.com", "TraceIsoB")
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

    resp = await client.get(f"/chat/messages/{message_id}/trace", headers=headers_b)
    assert resp.status_code == 404


# ---- Reranker-score confidence gate (RERANK_MIN_SCORE) --------------------------------
#
# The gate has no separate enable flag -- it's implicitly live whenever RERANKER_ENABLED
# is True, because rerank_score is None on every ContextBlock whenever it's False. These
# tests force a controlled rerank_score via a test-double Reranker overriding the real
# Depends(get_reranker) wiring.


class _FixedScoreReranker:
    """Test double: stamps every candidate with a caller-chosen fixed rerank_score,
    preserving order -- lets gate tests control the top block's score directly."""

    def __init__(self, score: float) -> None:
        self._score = score

    async def rerank(self, query: str, candidates: list[ChunkHit], top_k: int) -> list[ChunkHit]:
        return [hit.model_copy(update={"rerank_score": self._score}) for hit in candidates[:top_k]]


class _RecordingLLM:
    """Records how many times `stream` was called -- the only reliable proof the LLM seam
    was never invoked on the gate-fire path."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model(self) -> str:
        return "recording-llm"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.calls += 1
        for token in "should not be called".split(" "):
            yield token + " "


async def test_ask_weak_evidence_gate_fires_skips_llm_call(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=True + top block's rerank_score below RERANK_MIN_SCORE: the LLM
    seam is never called, the response carries weak_evidence=True, the fixed weak-evidence
    message, and zero citations."""
    from app.services.chat.service import _WEAK_EVIDENCE_MESSAGE

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 0.5)

    tokens = await _signup(client, "chat-weak-ask@test.com", "WeakAsk")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    recording_llm = _RecordingLLM()
    app.dependency_overrides[get_llm] = lambda: recording_llm
    app.dependency_overrides[get_reranker] = lambda: _FixedScoreReranker(score=0.1)
    try:
        resp = await client.post(
            "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
        )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["weak_evidence"] is True
    assert body["answer"] == _WEAK_EVIDENCE_MESSAGE
    assert body["citations"] == []
    assert recording_llm.calls == 0


async def test_ask_weak_evidence_gate_does_not_fire_above_threshold(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=True but the top block's rerank_score is ABOVE RERANK_MIN_SCORE:
    the normal LLM-call path proceeds unchanged, weak_evidence=False."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 0.5)

    tokens = await _signup(client, "chat-weak-above@test.com", "WeakAbove")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    app.dependency_overrides[get_reranker] = lambda: _FixedScoreReranker(score=0.9)
    try:
        resp = await client.post(
            "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
        )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["weak_evidence"] is False
    assert "[1]" in body["answer"]
    assert len(body["citations"]) == 1


async def test_ask_weak_evidence_gate_never_fires_when_reranker_disabled(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RERANKER_ENABLED=False (the default): rerank_score is always None regardless of
    what a reranker double would have returned (it's never even called), so the
    confidence gate structurally cannot fire. Uses an absurdly permissive-looking
    RERANK_MIN_SCORE and a reranker double that WOULD fail the threshold if it were ever
    consulted, to prove the gate is truly inert rather than coincidentally passing."""
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 999.0)

    tokens = await _signup(client, "chat-weak-off@test.com", "WeakOff")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    app.dependency_overrides[get_reranker] = lambda: _FixedScoreReranker(score=-1000.0)
    try:
        resp = await client.post(
            "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
        )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["weak_evidence"] is False
    assert "[1]" in body["answer"]


async def test_weak_evidence_gate_persists_trace_with_would_be_prompt(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On gate-fire, the assistant message + trace are persisted normally: the trace's
    final_prompt captures the prompt that WOULD have been sent (build_messages still ran,
    it's just never handed to the LLM), and raw_output is the fixed weak-evidence
    message."""
    from app.services.chat.service import _WEAK_EVIDENCE_MESSAGE

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 0.5)

    tokens = await _signup(client, "chat-weak-trace@test.com", "WeakTrace")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    app.dependency_overrides[get_reranker] = lambda: _FixedScoreReranker(score=0.1)
    try:
        resp = await client.post(
            "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
        )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    message_id = resp.json()["message_id"]
    trace_resp = await client.get(f"/chat/messages/{message_id}/trace", headers=headers)
    assert trace_resp.status_code == 200
    trace = trace_resp.json()
    assert trace["raw_output"] == _WEAK_EVIDENCE_MESSAGE
    assert "alpha" in trace["final_prompt"]
    assert len(trace["hits"]) == 1


async def test_stream_weak_evidence_gate_yields_only_done_event(
    client: AsyncClient, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSE variant: on gate-fire, zero token events are emitted (no LLM call happened),
    exactly one done event carries weak_evidence=True."""
    from app.services.chat.service import _WEAK_EVIDENCE_MESSAGE

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_MIN_SCORE", 0.5)

    tokens = await _signup(client, "chatstream-weak@test.com", "StreamWeak")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )

    app.dependency_overrides[get_reranker] = lambda: _FixedScoreReranker(score=0.1)
    try:
        resp = await client.post(
            "/chat/stream", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
        )
    finally:
        app.dependency_overrides.pop(get_reranker, None)

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(token_events) == 0
    assert len(done_events) == 1
    assert done_events[0]["weak_evidence"] is True
    assert done_events[0]["answer"] == _WEAK_EVIDENCE_MESSAGE
    assert done_events[0]["citations"] == []


# ---- message_feedback (POST /chat/messages/{message_id}/feedback) ---------------------


async def test_submit_feedback_then_resubmit_upserts_single_row(
    client: AsyncClient, session_factory
) -> None:
    """Upsert on (message_id, user_id): re-rating the same message updates the existing
    row's rating in place, never inserts a second row."""
    tokens = await _signup(client, "feedback-upsert@test.com", "FeedbackUpsert")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content about onboarding"
    )
    ask = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    message_id = ask.json()["message_id"]

    first = await client.post(
        f"/chat/messages/{message_id}/feedback", headers=headers, json={"rating": "up"}
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    second = await client.post(
        f"/chat/messages/{message_id}/feedback", headers=headers, json={"rating": "down"}
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.json()["rating"] == "down"

    async with session_factory() as session:
        from app.models.chat import MessageFeedback

        rows = (
            (
                await session.execute(
                    select(MessageFeedback).where(
                        MessageFeedback.message_id == uuid.UUID(message_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].rating == "down"


async def test_submit_feedback_on_user_message_400s(client: AsyncClient, session_factory) -> None:
    tokens = await _signup(client, "feedback-usermsg@test.com", "FeedbackUserMsg")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org_id = await _org_id(client, headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, headers, session_factory, org_id, "alpha content"
    )
    ask = await client.post(
        "/chat/ask", headers=headers, json={"notebook_id": notebook_id, "query": "alpha"}
    )
    conversation_id = ask.json()["conversation_id"]

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
        f"/chat/messages/{user_message_id}/feedback", headers=headers, json={"rating": "up"}
    )
    assert resp.status_code == 400


async def test_submit_feedback_nonexistent_message_404s(client: AsyncClient) -> None:
    tokens = await _signup(client, "feedback-missing@test.com", "FeedbackMissing")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post(
        f"/chat/messages/{uuid.uuid4()}/feedback", headers=headers, json={"rating": "up"}
    )
    assert resp.status_code == 404


async def test_submit_feedback_cross_org_message_404s(client: AsyncClient, session_factory) -> None:
    tokens_a = await _signup(client, "feedback-isoa@test.com", "FeedbackIsoA")
    tokens_b = await _signup(client, "feedback-isob@test.com", "FeedbackIsoB")
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
        f"/chat/messages/{message_id}/feedback", headers=headers_b, json={"rating": "up"}
    )
    assert resp.status_code == 404


async def test_submit_feedback_on_private_notebook_message_403s(
    client: AsyncClient, session_factory
) -> None:
    """A member who was never shared into the notebook (private by default per the
    2026-07-27 notebook-privacy feature) cannot rate a message in it — reuses the exact
    same NotebookAccessDenied check list_messages already performs."""
    owner_tokens = await _signup(client, "feedback-priv-owner@test.com", "FeedbackPrivOwner")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, owner_headers, session_factory, org_id, "private content"
    )
    ask = await client.post(
        "/chat/ask", headers=owner_headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]

    member_tokens = await _invite_member(client, owner_headers, "feedback-priv-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

    resp = await client.post(
        f"/chat/messages/{message_id}/feedback", headers=member_headers, json={"rating": "up"}
    )
    assert resp.status_code == 403


async def test_list_messages_my_feedback_scoped_per_user_no_leak(
    client: AsyncClient, session_factory
) -> None:
    """The calling user's own feedback shows up in list_messages; a DIFFERENT user's
    feedback on the same message must never leak into the first user's my_feedback."""
    owner_tokens = await _signup(client, "feedback-scope-owner@test.com", "FeedbackScopeOwner")
    owner_headers = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    org_id = await _org_id(client, owner_headers)
    notebook_id, _doc_id = await _make_notebook_with_document(
        client, owner_headers, session_factory, org_id, "shared content"
    )
    ask = await client.post(
        "/chat/ask", headers=owner_headers, json={"notebook_id": notebook_id, "query": "q"}
    )
    message_id = ask.json()["message_id"]

    member_tokens = await _invite_member(client, owner_headers, "feedback-scope-member@test.com")
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
    member_me = (await client.get("/auth/me", headers=member_headers)).json()
    share = await client.post(
        f"/notebooks/{notebook_id}/shares",
        headers=owner_headers,
        json={"user_id": member_me["id"]},
    )
    assert share.status_code == 204

    owner_rate = await client.post(
        f"/chat/messages/{message_id}/feedback", headers=owner_headers, json={"rating": "up"}
    )
    assert owner_rate.status_code == 200

    member_history = await client.get(
        f"/chat/notebooks/{notebook_id}/messages", headers=member_headers
    )
    assert member_history.status_code == 200
    member_assistant_msg = next(m for m in member_history.json() if m["role"] == "assistant")
    assert member_assistant_msg["my_feedback"] is None  # owner's rating doesn't leak

    member_rate = await client.post(
        f"/chat/messages/{message_id}/feedback", headers=member_headers, json={"rating": "down"}
    )
    assert member_rate.status_code == 200

    owner_history = await client.get(
        f"/chat/notebooks/{notebook_id}/messages", headers=owner_headers
    )
    owner_assistant_msg = next(m for m in owner_history.json() if m["role"] == "assistant")
    assert owner_assistant_msg["my_feedback"] == "up"  # owner's own rating still shows

    member_history_2 = await client.get(
        f"/chat/notebooks/{notebook_id}/messages", headers=member_headers
    )
    member_assistant_msg_2 = next(m for m in member_history_2.json() if m["role"] == "assistant")
    assert member_assistant_msg_2["my_feedback"] == "down"  # member's own rating shows
