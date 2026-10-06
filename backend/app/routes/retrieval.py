"""Retrieval routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.retrieval import SearchPageResponse, SparseSearchResponse

router = APIRouter(prefix="/retrieval", tags=["retrieval"])

router.post("/search", response_model=SearchPageResponse)(controllers.retrieval.search)
router.post("/sparse-search", response_model=SparseSearchResponse)(
    controllers.retrieval.sparse_search
)
