"""F20 parsing stage — the first stage of the staged ingestion pipeline
(architecture.md "Ingestion pipeline"). Calls into ``documents.service`` for every
document-table mutation (module boundary rule: a module reaches another module only
through its service, never its repository); reaches the ``Parser`` seam and the object
store (not a seam — called directly) to do the actual work.
"""

from __future__ import annotations

import json
import uuid

from app.documents.schemas import DocumentOut
from app.documents.service import documents_service
from app.documents.status import DocumentStatus
from app.platform.context import TenantContext
from app.platform.logging import get_logger
from app.platform.seams import ParsedDoc, Parser
from app.platform.storage import ObjectStore, build_artifact_key

logger = get_logger(__name__)


def _serialize_artifact(parsed: ParsedDoc) -> bytes:
    """The parser artifact persisted for F21 structuring to consume: raw text + outline,
    so the next stage never re-parses. Degenerate-outline handling (no headings) is F21's
    concern, not parsing's — this just persists whatever the seam returned."""
    payload = {
        "text": parsed.text,
        "language": parsed.language,
        "page_count": parsed.page_count,
        "outline": [
            {
                "heading": node.heading,
                "level": node.level,
                "char_start": node.char_start,
                "char_end": node.char_end,
                "page_start": node.page_start,
                "page_end": node.page_end,
            }
            for node in parsed.outline
        ],
    }
    return json.dumps(payload).encode("utf-8")


class IngestionService:
    async def run_parsing_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        parser: Parser,
        object_store: ObjectStore,
    ) -> DocumentOut:
        """UPLOADED -> PARSING -> STRUCTURING, or -> FAILED with failed_stage=PARSING.

        Idempotent and resumable: a document already past parsing (STRUCTURING or later) is
        returned unchanged with no seam/object-store calls; a document stuck in PARSING or
        previously FAILED at this stage is retried from scratch.
        """
        document = await documents_service.begin_parsing(ctx, document_id)
        if document.status != DocumentStatus.PARSING:
            return document

        try:
            blob = await object_store.get(document.storage_key)
            parsed = await parser.extract(blob, document.mime_type or "application/octet-stream")
            artifact_key = build_artifact_key(ctx.org_id, document.id, "parsing")
            await object_store.put(artifact_key, _serialize_artifact(parsed), "application/json")
        except Exception as exc:  # the only seam/IO call here — record, never swallow
            logger.warning(
                "ingestion.parsing_failed",
                document_id=str(document_id),
                org_id=str(ctx.org_id),
                error=str(exc),
            )
            return await documents_service.fail_stage(
                ctx,
                document_id,
                failed_stage=DocumentStatus.PARSING.value,
                error_detail=str(exc),
            )

        return await documents_service.complete_parsing(
            ctx,
            document_id,
            language=parsed.language,
            page_count=parsed.page_count,
            artifact_key=artifact_key,
        )


ingestion_service = IngestionService()
