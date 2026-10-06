"""U10: chat history must come back in a deterministic order.

``/chat/ask`` writes the user question and assistant answer in ONE transaction, so both
rows share the same ``created_at`` (Postgres ``now()`` is the transaction timestamp).
``MessageRepository.list_for_notebook`` must therefore break the tie itself — user
before assistant — rather than leave it to the planner/heap order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.chat import Conversation, Message
from main import app


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_history_orders_user_before_assistant_when_created_at_ties(
    client: AsyncClient, session_factory
) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "histord-a@test.com", "password": "password123", "org_name": "HistOrd"},
    )
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    org_id = uuid.UUID((await client.get("/auth/me", headers=headers)).json()["org_id"])
    nb = await client.post("/notebooks", headers=headers, json={"name": "Order NB"})
    assert nb.status_code == 201
    notebook_id = uuid.UUID(nb.json()["id"])

    same_ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    later_ts = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
    conv_1, conv_2 = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Conversation(id=conv_1, org_id=org_id, knowledge_base_id=notebook_id),
                Conversation(id=conv_2, org_id=org_id, knowledge_base_id=notebook_id),
            ]
        )
        await session.flush()
        # Assistant rows get the LOWEST ids and are inserted first, so neither id order
        # nor insertion order can accidentally produce the correct result.
        session.add_all(
            [
                Message(
                    id=uuid.UUID(int=1),
                    org_id=org_id,
                    conversation_id=conv_1,
                    role="assistant",
                    content="answer one",
                    created_at=same_ts,
                ),
                Message(
                    id=uuid.UUID(int=2),
                    org_id=org_id,
                    conversation_id=conv_2,
                    role="assistant",
                    content="answer two",
                    created_at=later_ts,
                ),
                Message(
                    id=uuid.UUID(int=2**128 - 1),
                    org_id=org_id,
                    conversation_id=conv_1,
                    role="user",
                    content="question one",
                    created_at=same_ts,
                ),
                Message(
                    id=uuid.UUID(int=2**128 - 2),
                    org_id=org_id,
                    conversation_id=conv_2,
                    role="user",
                    content="question two",
                    created_at=later_ts,
                ),
            ]
        )

    for _ in range(3):  # stable across repeated reads, not just once
        resp = await client.get(f"/chat/notebooks/{notebook_id}/messages", headers=headers)
        assert resp.status_code == 200
        assert [m["content"] for m in resp.json()] == [
            "question one",
            "answer one",
            "question two",
            "answer two",
        ]
