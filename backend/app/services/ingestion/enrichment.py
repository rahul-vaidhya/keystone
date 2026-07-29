"""V2 enrichment stage: summarize and embed sections; never mutates document status.

Flag-gated and additive: a document reaching READY is queryable via flat retrieval
regardless of enrichment success or completion; section summaries + section embeddings are
bonus metadata for future V2 hierarchical retrieval. If enrichment fails at any point,
the document stays READY and queryable, unaffected."""

from __future__ import annotations

import json
import uuid

from app.config import db as db_mod
from app.config.logging import get_logger
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut, DocumentStatus
from app.services.documents import documents_service
from app.services.ingestion.repository import (
    ChunkRepository,
    EmbeddingRepository,
    SectionRepository,
)
from app.services.seams import LLM, Embedder, Message

logger = get_logger(__name__)


def _parse_enrichment(raw: str) -> tuple[str, list[str]]:
    """Parses an LLM enrichment response: JSON with ``summary`` (str, max 2000 chars) and
    ``topics`` (list of str, max 5 items, each max 80 chars).

    Strips leading/trailing code fences if present; missing/invalid fields degrade to
    null summary or empty topics list. Raises ValueError if json.loads fails."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        # Strip code fences: ```json\n...\n``` or just ```\n...\n```
        stripped = stripped.lstrip("`").lstrip("json").strip()
        stripped = stripped.rstrip("`").strip()

    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("Response is not a JSON object")

    summary = parsed.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    summary = summary.strip()[:2000]  # cap at 2000 chars

    topics = parsed.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    # Cap at 5 topics, each capped at 80 chars
    topics = [str(t).strip()[:80] for t in topics[:5] if t]

    return summary, topics


async def run_enrichment_stage(
    ctx: TenantContext,
    document_id: uuid.UUID,
    *,
    llm: LLM,
    embedder: Embedder,
    object_store,
) -> DocumentOut:
    """Enrich a READY document by generating summaries and embeddings for each section.

    Idempotent: re-running on a document already enriched upserts the section embeddings
    (same unique(owner_type, owner_id, model) constraint). Additive and failure-tolerant:
    if any section fails to enrich, that section is skipped; if all sections fail,
    the document is returned unchanged. Document status never changes — enrichment is
    purely metadata augmentation.
    """
    # Fetch and check the document — if not READY, no-op and return unchanged.
    document = await documents_service.get_document(ctx, document_id)
    if document.status != DocumentStatus.READY:
        logger.info(
            "ingestion.enrichment_skipped_not_ready",
            document_id=str(document_id),
            org_id=str(ctx.org_id),
            status=document.status,
        )
        return document

    try:
        # Load the parsing artifact to get the raw text.
        artifact_key = await documents_service.get_parse_artifact_key(ctx, document_id)
        raw = await object_store.get(artifact_key)
        artifact = json.loads(raw)
        text = artifact["text"]

        # Load sections from the database.
        async with db_mod.tenant_session(ctx.org_id) as session:
            sections = await SectionRepository(session, ctx).list_for_document(document_id)

        # Enrich each section in sequence.
        successful_rows: list[dict] = []
        for section in sections:
            # Extract the section's text segment.
            segment = text[section.char_start : section.char_end].strip()[
                : settings.ENRICHMENT_SECTION_CHAR_LIMIT
            ]
            if not segment:
                continue

            # Build the enrichment prompt.
            heading = section.heading or "(untitled)"
            system_prompt = (
                "You summarize one section of a document. Respond with ONLY a JSON object, "
                "no prose, no code fences."
            )
            user_prompt = (
                f"Section heading: {heading}\n\n"
                f"Instructions: Return a JSON object with exactly two fields:\n"
                f"  - 'summary': a 2-3 sentence summary of this section (string)\n"
                f"  - 'topics': an array of up to 5 short topic strings that describe\n"
                f"    the key concepts\n\n"
                f"Section text:\n{segment}"
            )

            try:
                # Call the LLM and consume the stream to completion.
                raw_output = ""
                async for token in llm.stream(
                    [
                        Message(role="system", content=system_prompt),
                        Message(role="user", content=user_prompt),
                    ]
                ):
                    raw_output += token

                # Parse the response.
                summary, topics = _parse_enrichment(raw_output)
                if not summary:
                    # If parsing fails or summary is empty, skip this section.
                    logger.warning(
                        "ingestion.enrichment_section_failed",
                        section_id=str(section.id),
                        document_id=str(document_id),
                        org_id=str(ctx.org_id),
                        reason="empty_summary_after_parse",
                    )
                    continue

                successful_rows.append(
                    {
                        "id": section.id,
                        "summary": summary,
                        "topics": topics,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "ingestion.enrichment_section_failed",
                    section_id=str(section.id),
                    document_id=str(document_id),
                    org_id=str(ctx.org_id),
                    error=str(exc),
                )
                continue

        # If no sections succeeded, log and return the document unchanged.
        if not successful_rows:
            logger.info(
                "ingestion.enrichment_no_sections",
                document_id=str(document_id),
                org_id=str(ctx.org_id),
            )
            return document

        # Embed the summaries and upsert the embeddings in a single transaction.
        summaries = [row["summary"] for row in successful_rows]
        vectors = await embedder.embed(summaries)

        async with db_mod.tenant_session(ctx.org_id) as session:
            # Update the sections with their summaries and topics.
            await SectionRepository(session, ctx).update_enrichment(successful_rows)

            # Prepare embedding rows and upsert them as owner_type='section'.
            embedding_rows = [
                {
                    "owner_id": row["id"],
                    "model": embedder.model,
                    "dim": embedder.dim,
                    "embedding": vector,
                }
                for row, vector in zip(successful_rows, vectors, strict=True)
            ]
            await EmbeddingRepository(session, ctx).upsert_embeddings(
                document_id, "section", embedding_rows
            )

        # P1 "contextual retrieval" (memory.md "P1 roadmap"): re-embed each enriched
        # section's chunks IN PLACE, prepending the section's own summary (just computed
        # above) to each chunk's raw text before re-embedding — reuses the EXISTING
        # owner_type='chunk' rows/upsert constraint, zero new LLM calls, zero schema
        # change. Deliberately per-section with its own try/except (same non-fatal
        # discipline as the summary/topics extraction loop above): a failure to re-embed
        # one section's chunks must never break enrichment for other sections or fail the
        # whole document. Only takes effect once a section's summary has been computed
        # (i.e. this round of enrichment, or a prior one) — a document with
        # CONTEXTUAL_EMBEDDING_ENABLED off, or not yet enriched at all, keeps its original
        # context-free chunk embeddings untouched.
        if settings.CONTEXTUAL_EMBEDDING_ENABLED:
            for row in successful_rows:
                try:
                    async with db_mod.tenant_session(ctx.org_id) as session:
                        section_chunks = await ChunkRepository(session, ctx).list_for_sections(
                            [row["id"]]
                        )
                    if not section_chunks:
                        continue

                    contextualized_texts = [
                        f"{row['summary']}\n\n{chunk.content}" for chunk in section_chunks
                    ]
                    vectors = await embedder.embed(contextualized_texts)

                    chunk_embedding_rows = [
                        {
                            "owner_id": chunk.id,
                            "model": embedder.model,
                            "dim": embedder.dim,
                            "embedding": vector,
                        }
                        for chunk, vector in zip(section_chunks, vectors, strict=True)
                    ]
                    async with db_mod.tenant_session(ctx.org_id) as session:
                        await EmbeddingRepository(session, ctx).upsert_chunk_embeddings(
                            document_id, chunk_embedding_rows
                        )

                    logger.info(
                        "ingestion.contextual_embedding_section_reembedded",
                        section_id=str(row["id"]),
                        document_id=str(document_id),
                        org_id=str(ctx.org_id),
                        chunk_count=len(section_chunks),
                    )
                except Exception as exc:
                    logger.warning(
                        "ingestion.contextual_embedding_section_failed",
                        section_id=str(row["id"]),
                        document_id=str(document_id),
                        org_id=str(ctx.org_id),
                        error=str(exc),
                    )
                    continue

        logger.info(
            "ingestion.enrichment_succeeded",
            document_id=str(document_id),
            org_id=str(ctx.org_id),
            enriched_section_count=len(successful_rows),
        )

    except Exception as exc:
        # Enrichment failures are non-fatal: log and return the document unchanged.
        # This is a deliberate deviation from the F20-F22 terminal-stage-goes-FAILED
        # pattern: the document is already READY/queryable; enrichment is additive
        # metadata. Persisting failures to a separate enrichment status would complicate
        # the data model; instead, we accept that a transient enrichment failure will
        # be retried on the next enrichment run (or silently dropped if the flag is
        # disabled going forward).
        logger.warning(
            "ingestion.enrichment_failed",
            document_id=str(document_id),
            org_id=str(ctx.org_id),
            error=str(exc),
        )

    return await documents_service.get_document(ctx, document_id)
