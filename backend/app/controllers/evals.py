"""Evals HTTP handlers. Thin (all logic in service). Both endpoints are admin-gated
(``require_admin``), same precedent as chat's F42 admin debug bundle (``get_trace``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.middleware.context import TenantContext
from app.middleware.deps import require_admin
from app.models.evals import GoldenQuestionCreate, GoldenQuestionOut
from app.services.evals import evals_service


async def create_golden_question(
    req: GoldenQuestionCreate,
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> GoldenQuestionOut:
    return await evals_service.create_from_message(ctx, req.message_id)


async def list_golden_questions(
    ctx: Annotated[TenantContext, Depends(require_admin)],
) -> list[GoldenQuestionOut]:
    return await evals_service.list_golden_questions(ctx)
