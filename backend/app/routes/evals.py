"""Evals routes — wires the router and decorators; logic stays in controllers."""

from __future__ import annotations

from fastapi import APIRouter

from app import controllers
from app.models.evals import GoldenQuestionOut

router = APIRouter(prefix="/evals", tags=["evals"])

router.post("/golden-questions", response_model=GoldenQuestionOut, status_code=201)(
    controllers.evals.create_golden_question
)
router.get("/golden-questions", response_model=list[GoldenQuestionOut])(
    controllers.evals.list_golden_questions
)
