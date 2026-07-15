"""F00 smoke: the FastAPI and arq entrypoints import and expose what they should."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from worker import WorkerSettings


def test_health_returns_200() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_worker_has_redis_settings() -> None:
    # F24+enrichment: the ingestion pipeline's 4 stage jobs are registered (was [] through F23).
    assert len(WorkerSettings.functions) == 4
    assert WorkerSettings.redis_settings is not None


def test_worker_configures_logging_on_startup() -> None:
    # F02: workers emit the same JSON logs as the HTTP edge via this lifecycle hook.
    assert callable(WorkerSettings.on_startup)
