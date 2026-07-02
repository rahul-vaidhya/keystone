"""Ingestion routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.documents import DocumentOut

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

router.post("/documents/{document_id}/parse", response_model=DocumentOut)(
    controllers.ingestion.parse_document
)
router.post("/documents/{document_id}/structure", response_model=DocumentOut)(
    controllers.ingestion.structure_document
)
router.post("/documents/{document_id}/embed", response_model=DocumentOut)(
    controllers.ingestion.embed_document
)
