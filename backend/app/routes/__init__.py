"""Routes — FastAPI router registration for all domains."""

from __future__ import annotations

from app.routes import auth, chat, documents, ingestion, notebooks, retrieval

__all__ = ["auth", "chat", "documents", "ingestion", "notebooks", "retrieval"]
