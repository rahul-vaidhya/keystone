"""F20 parsing stage: parser seam → persist raw text + structure artifact; set
language/page_count. See architecture.md "Ingestion pipeline"."""

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


async def run_parsing_stage(
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

    artifact_key = build_artifact_key(ctx.org_id, document.id, "parsing")
    try:
        # Idempotent re-run: a prior attempt may have already persisted the artifact
        # (e.g. crashed after `put` but before the status write) — reuse it instead of
        # re-calling the parser, which would re-pay any OCR cost on a real parser.
        try:
            existing = await object_store.get(artifact_key)
        except Exception:
            existing = None

        if existing is not None:
            artifact = json.loads(existing)
            language, page_count = artifact["language"], artifact["page_count"]
        else:
            blob = await object_store.get(document.storage_key)
            parsed = await parser.extract(blob, document.mime_type or "application/octet-stream")
            await object_store.put(artifact_key, _serialize_artifact(parsed), "application/json")
            language, page_count = parsed.language, parsed.page_count
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
        language=language,
        page_count=page_count,
        artifact_key=artifact_key,
    )
