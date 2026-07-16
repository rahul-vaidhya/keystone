"""Ingestion routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.documents import DocumentOut
from app.models.ingestion import EnrichmentBackfillResult

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
router.post("/documents/{document_id}/enrich", response_model=DocumentOut)(
    controllers.ingestion.enrich_document
)
router.post("/enrich-backfill", response_model=EnrichmentBackfillResult)(
    controllers.ingestion.enrich_backfill
)
