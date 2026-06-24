"""F24 ingestion pipeline auto-dispatch tests.

Covers: upload enqueues the parsing job (and a dedupe hit does not); the job chain
advances stage-by-stage purely by enqueueing the next job on success; a redelivered job
is a safe no-op (no duplicate next-stage enqueue); and job-level org-scoping is an
independent backstop (a job built from one org's payload can never touch another org's
document), same pattern as F31's ``search_chunks`` isolation test.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.documents.exceptions import DocumentNotFound
from app.ingestion.tasks import (
    run_embedding_stage_job,
    run_parsing_stage_job,
    run_structuring_stage_job,
)
from app.platform.queue import get_job_queue
from app.platform.storage import get_object_store
from main import app
from tests.conftest import FakeJobQueue


class _InMemoryObjectStore:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]


@pytest.fixture
async def store() -> _InMemoryObjectStore:
    return _InMemoryObjectStore()


@pytest.fixture
async def job_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
async def client(session_factory, tenant_engine, store, job_queue) -> AsyncClient:
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_job_queue, None)


async def _signup(client: AsyncClient, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/auth/signup", json={"email": email, "password": "password123", "org_name": org_name}
    )
    assert resp.status_code == 201
    return resp.json()


async def _upload(client: AsyncClient, headers: dict, content: bytes = b"hello world") -> dict:
    resp = await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("handbook.pdf", content, "application/pdf")},
    )
    return resp


async def test_upload_enqueues_parsing_job(client: AsyncClient, job_queue: FakeJobQueue) -> None:
    tokens = await _signup(client, "dispatch-up1@test.com", "DispatchOne")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await _upload(client, headers)
    assert resp.status_code == 201
    doc = resp.json()

    assert job_queue.calls == [
        ("run_parsing_stage_job", {"org_id": doc["org_id"], "document_id": doc["id"]})
    ]


async def test_dedupe_upload_does_not_re_enqueue(
    client: AsyncClient, job_queue: FakeJobQueue
) -> None:
    tokens = await _signup(client, "dispatch-up2@test.com", "DispatchTwo")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    first = await _upload(client, headers)
    assert first.status_code == 201

    second = await _upload(client, headers)  # same bytes -> checksum dedupe hit
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    assert len(job_queue.calls) == 1  # only the first (genuine) upload enqueued


async def test_job_chain_advances_through_all_stages_via_enqueue(
    client: AsyncClient, store: _InMemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ingestion.tasks.get_object_store", lambda: store)

    tokens = await _signup(client, "dispatch-up3@test.com", "DispatchThree")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = (await _upload(client, headers)).json()

    chain_queue = FakeJobQueue()
    org_id, document_id = doc["org_id"], doc["id"]

    await run_parsing_stage_job({"job_queue": chain_queue}, org_id=org_id, document_id=document_id)
    assert chain_queue.calls == [
        ("run_structuring_stage_job", {"org_id": org_id, "document_id": document_id})
    ]

    await run_structuring_stage_job(
        {"job_queue": chain_queue}, org_id=org_id, document_id=document_id
    )
    assert chain_queue.calls[-1] == (
        "run_embedding_stage_job",
        {"org_id": org_id, "document_id": document_id},
    )

    await run_embedding_stage_job(
        {"job_queue": chain_queue}, org_id=org_id, document_id=document_id
    )
    # Terminal stage enqueues nothing further.
    assert len(chain_queue.calls) == 2


async def test_sequentially_redelivered_job_does_not_double_enqueue(
    client: AsyncClient, store: _InMemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The before/after status check (the optimization layer, not the correctness
    guarantee — see ``ingestion/tasks.py``'s module docstring) handles the common,
    sequential-redelivery case: a job that runs again after the document has already
    advanced is a clean no-op."""
    monkeypatch.setattr("app.ingestion.tasks.get_object_store", lambda: store)

    tokens = await _signup(client, "dispatch-up4@test.com", "DispatchFour")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    doc = (await _upload(client, headers)).json()

    chain_queue = FakeJobQueue()
    org_id, document_id = doc["org_id"], doc["id"]

    # First delivery: real work happens, next stage enqueued exactly once.
    await run_parsing_stage_job({"job_queue": chain_queue}, org_id=org_id, document_id=document_id)
    assert len(chain_queue.calls) == 1

    # Redelivery of the SAME job (arq is at-least-once): the document is already past
    # PARSING, so this must be a safe no-op — no second structuring-job enqueue.
    await run_parsing_stage_job({"job_queue": chain_queue}, org_id=org_id, document_id=document_id)
    assert len(chain_queue.calls) == 1


async def test_job_id_dedup_is_the_correctness_backstop_under_concurrent_redelivery() -> None:
    """The before/after check alone CANNOT catch a concurrent redelivery: two deliveries
    running close together can both read the same "before" status (in separate
    transactions from the actual stage claim) and both decide to enqueue. This test
    proves the real guarantee directly at the queue level — arq's ``_job_id`` dedup
    (modelled by ``FakeJobQueue``) drops a second enqueue sharing an already-seen job_id,
    regardless of what application-level logic decided to call it. This is what makes the
    chain correct even when the before/after gate races (review finding, fixed)."""
    queue = FakeJobQueue()
    job_id = "ingestion:structuring:doc-1"

    await queue.enqueue(
        "run_structuring_stage_job", job_id=job_id, org_id="org-1", document_id="doc-1"
    )
    # A second, independently-decided enqueue with the SAME job_id (simulating a second
    # concurrent delivery that also computed before != after) must be dropped.
    await queue.enqueue(
        "run_structuring_stage_job", job_id=job_id, org_id="org-1", document_id="doc-1"
    )

    assert len(queue.calls) == 1


async def test_job_org_scoping_is_an_independent_backstop(
    client: AsyncClient, store: _InMemoryObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job built from org A's payload must never touch org B's document — independent of
    whatever enqueued it, same backstop pattern as F31's ``search_chunks`` isolation test."""
    monkeypatch.setattr("app.ingestion.tasks.get_object_store", lambda: store)

    tokens_a = await _signup(client, "dispatch-orga@test.com", "DispatchOrgA")
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}
    doc_a = (await _upload(client, headers_a)).json()

    tokens_b = await _signup(client, "dispatch-orgb@test.com", "DispatchOrgB")
    headers_b = {"Authorization": f"Bearer {tokens_b['access_token']}"}
    me_b = await client.get("/auth/me", headers=headers_b)
    org_b_id = me_b.json()["org_id"]

    chain_queue = FakeJobQueue()
    with pytest.raises(DocumentNotFound):
        await run_parsing_stage_job(
            {"job_queue": chain_queue}, org_id=org_b_id, document_id=doc_a["id"]
        )
    assert chain_queue.calls == []  # never reached the point of enqueueing anything

    # Same-org control: the identical call succeeds when org_id matches.
    await run_parsing_stage_job(
        {"job_queue": chain_queue}, org_id=doc_a["org_id"], document_id=doc_a["id"]
    )
    assert len(chain_queue.calls) == 1
