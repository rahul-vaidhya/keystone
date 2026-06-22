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
    assert WorkerSettings.functions == []
    assert WorkerSettings.redis_settings is not None
