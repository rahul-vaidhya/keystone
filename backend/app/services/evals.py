"""Golden-eval curation use cases.

Grows an admin-curatable ``golden_questions`` table from a real graded ``/chat/ask``
answer via ``chat_service.get_curation_snapshot`` — the ONLY way this module reads chat
data (module-boundary rule: a domain's service calls another domain only through its
``services/<other>.py``, never its repository classes or ORM models directly).

Flat file (package-layout convention: a new, small domain starts flat — this one file
holds both the repository class and the service, well under the 200-line/2-independent-
responsibility promotion trigger).
"""

from __future__ import annotations

import uuid

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.evals import GoldenQuestion, GoldenQuestionOut
from app.services.base import BaseRepository
from app.services.chat import chat_service


# ---- repository ----
class GoldenQuestionRepository(BaseRepository[GoldenQuestion]):
    model = GoldenQuestion

    async def create(
        self,
        *,
        notebook_id: uuid.UUID,
        source_message_id: uuid.UUID | None,
        question: str,
        reference_answer: str,
        reference_contexts: list[str],
        created_by: uuid.UUID | None,
    ) -> GoldenQuestion:
        golden_question = GoldenQuestion(
            org_id=self._ctx.org_id,
            notebook_id=notebook_id,
            source_message_id=source_message_id,
            question=question,
            reference_answer=reference_answer,
            reference_contexts=reference_contexts,
            created_by=created_by,
        )
        self._db.add(golden_question)
        await self._db.flush()
        return golden_question

    async def list_active(self, notebook_id: uuid.UUID | None = None) -> list[GoldenQuestion]:
        stmt = self._scoped().where(GoldenQuestion.status == "active")
        if notebook_id is not None:
            stmt = stmt.where(GoldenQuestion.notebook_id == notebook_id)
        stmt = stmt.order_by(GoldenQuestion.created_at)
        return list(await self._db.scalars(stmt))


# ---- service ----
class EvalsService:
    async def create_from_message(
        self, ctx: TenantContext, message_id: uuid.UUID
    ) -> GoldenQuestionOut:
        """Curates a golden question from a real graded answer. Everything but
        ``created_by`` is derived server-side from the message's trace via
        ``chat_service.get_curation_snapshot`` — the caller only supplies WHICH message
        to curate. Propagates ``chat_service``'s ``MessageNotFound`` /
        ``MessageTraceNotFound`` / ``CannotCurateUserMessage`` unchanged (all three
        already map to the correct HTTP status via the central exception handlers —
        no evals-specific wrapping needed)."""
        snapshot = await chat_service.get_curation_snapshot(ctx, message_id)
        async with db_mod.tenant_session(ctx.org_id) as session:
            golden_question = await GoldenQuestionRepository(session, ctx).create(
                notebook_id=snapshot.notebook_id,
                source_message_id=message_id,
                question=snapshot.question,
                reference_answer=snapshot.reference_answer,
                reference_contexts=snapshot.reference_contexts,
                created_by=ctx.user_id,
            )
        return GoldenQuestionOut.model_validate(golden_question)

    async def list_golden_questions(self, ctx: TenantContext) -> list[GoldenQuestionOut]:
        async with db_mod.tenant_session(ctx.org_id) as session:
            golden_questions = await GoldenQuestionRepository(session, ctx).list_active()
        return [GoldenQuestionOut.model_validate(g) for g in golden_questions]


evals_service = EvalsService()
