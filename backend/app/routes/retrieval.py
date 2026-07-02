"""Retrieval routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.retrieval import RetrievalSearchResponse

router = APIRouter(prefix="/retrieval", tags=["retrieval"])

router.post("/search", response_model=RetrievalSearchResponse)(controllers.retrieval.search)
