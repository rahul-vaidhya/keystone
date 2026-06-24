"""FastAPI entrypoint. Run with `uvicorn main:app`.

Feature routers are mounted here via ``app.include_router(...)``; no business logic lives
in this file.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.chat.router import router as chat_router
from app.documents.router import router as documents_router
from app.identity.router import router as auth_router
from app.ingestion.router import router as ingestion_router
from app.knowledge.router import router as knowledge_router
from app.platform.config import settings
from app.platform.http import register_exception_handlers
from app.platform.logging import configure_logging
from app.retrieval.router import router as retrieval_router

configure_logging()

app = FastAPI(title="Veratas", version="0.1.0")

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(ingestion_router)
app.include_router(knowledge_router)
app.include_router(retrieval_router)
app.include_router(chat_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
