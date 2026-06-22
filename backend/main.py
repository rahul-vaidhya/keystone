"""FastAPI entrypoint. Run with `uvicorn main:app`.

Phase 0 ships only a liveness probe. Feature routers are mounted here in later phases
via `app.include_router(...)`; no business logic lives in this file.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.platform.logging import configure_logging

configure_logging()

app = FastAPI(title="Veratas", version="0.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
