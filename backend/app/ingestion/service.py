"""F20 parsing + F21 structuring stages — the first two stages of the staged ingestion
pipeline (architecture.md "Ingestion pipeline"). Calls into ``documents.service`` for
every document-table mutation (module boundary rule: a module reaches another module
only through its service, never its repository); reaches the ``Parser`` seam and the
object store (not a seam — called directly) to do the actual work.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from app.documents.schemas import DocumentOut
from app.documents.service import documents_service
from app.documents.status import DocumentStatus
from app.ingestion.models import Chunk, Section
from app.ingestion.repository import ChunkRepository, EmbeddingRepository, SectionRepository
from app.platform import db as db_mod
from app.platform.context import TenantContext
from app.platform.logging import get_logger
from app.platform.seams import Embedder, ParsedDoc, Parser
from app.platform.storage import ObjectStore, build_artifact_key

logger = get_logger(__name__)

# Target chunk window, in characters — no tokenizer dependency yet, so token_count is a
# rough heuristic (len(content) // 4), not an exact count.
CHUNK_TARGET_CHARS = 1000


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


@dataclass
class _SectionNode:
    """Working tree node while building the ``sections`` hierarchy from the parser's
    flat outline — not persisted directly; ``_build_sections_and_chunks`` turns each
    one into a ``Section`` ORM row."""

    heading: str | None
    depth: int
    ordinal: int
    char_start: int
    char_end: int
    page_start: int
    page_end: int
    path: str = ""
    children: list[_SectionNode] = field(default_factory=list)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def _build_section_nodes(outline: list[dict], text_len: int, page_count: int) -> list[_SectionNode]:
    """Reconstructs the nested tree from the outline's flat (heading, level) list using a
    stack keyed on level: a node deeper than the stack top becomes its child; shallower or
    equal pops until a lower-level parent is found. Document order in the outline list is
    preserved as tree order.

    Degenerate-outline contract (architecture.md): an empty outline yields ONE root section
    spanning the full char range, so every chunk still has a section to attach to.
    """
    if not outline:
        root = _SectionNode(
            heading=None,
            depth=0,
            ordinal=0,
            char_start=0,
            char_end=text_len,
            page_start=1,
            page_end=max(page_count, 1),
            path="1",
        )
        return [root]

    top_level: list[_SectionNode] = []
    stack: list[tuple[int, _SectionNode]] = []
    for entry in outline:
        level = entry["level"]
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            parent = stack[-1][1]
            siblings = parent.children
            depth = parent.depth + 1
            path_prefix = parent.path
        else:
            siblings = top_level
            depth = 1
            path_prefix = None
        ordinal = len(siblings)
        path = f"{path_prefix}.{ordinal + 1}" if path_prefix else str(ordinal + 1)
        node = _SectionNode(
            heading=entry["heading"],
            depth=depth,
            ordinal=ordinal,
            char_start=entry["char_start"],
            char_end=entry["char_end"],
            page_start=entry["page_start"],
            page_end=entry["page_end"],
            path=path,
        )
        siblings.append(node)
        stack.append((level, node))
    return top_level


def _flatten_preorder(nodes: list[_SectionNode], parent: _SectionNode | None = None):
    """Pre-order walk: a node before its children — matches document order since a
    parent's char range always starts before its children's."""
    for node in nodes:
        yield node, parent
        yield from _flatten_preorder(node.children, node)


def _split_into_windows(
    text: str, start: int, end: int, target: int = CHUNK_TARGET_CHARS
) -> list[tuple[int, int]]:
    """Splits ``text[start:end]`` into ~``target``-char windows, breaking on the nearest
    preceding space so words aren't cut mid-token where avoidable."""
    windows: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        window_end = min(pos + target, end)
        if window_end < end:
            break_at = text.rfind(" ", pos, window_end)
            if break_at > pos:
                window_end = break_at + 1
        windows.append((pos, window_end))
        pos = window_end
    return windows


def _chunk_id(document_id: uuid.UUID, ordinal: int, content: str) -> uuid.UUID:
    """Deterministic id (codestandards "Ingestion correctness": ``hash(document_id,
    ordinal, content)``) — same chunk content at the same position always gets the same id."""
    digest = hashlib.sha256(f"{document_id}|{ordinal}|{content}".encode()).digest()
    return uuid.UUID(bytes=digest[:16])


def _build_sections_and_chunks(
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    text: str,
    outline: list[dict],
    page_count: int,
) -> tuple[list[Section], list[Chunk]]:
    """Builds the full set of ``Section``/``Chunk`` rows for a document. Chunks are only
    created for leaf sections — a leaf's char range is never re-covered by a child — so
    every chunk lands on exactly one section by construction (no chunk is ever orphaned)."""
    nodes = _build_section_nodes(outline, len(text), page_count)
    sections: list[Section] = []
    chunks: list[Chunk] = []
    ordinal = 0
    for node, parent in _flatten_preorder(nodes):
        sections.append(
            Section(
                id=node.id,
                org_id=org_id,
                document_id=document_id,
                parent_section_id=parent.id if parent else None,
                ordinal=node.ordinal,
                depth=node.depth,
                path=node.path,
                heading=node.heading,
                page_start=node.page_start,
                page_end=node.page_end,
                char_start=node.char_start,
                char_end=node.char_end,
            )
        )
        if node.children:
            continue  # only leaves get chunks — their range is never double-covered
        for start, end in _split_into_windows(text, node.char_start, node.char_end):
            content = text[start:end]
            if not content.strip():
                continue
            chunks.append(
                Chunk(
                    id=_chunk_id(document_id, ordinal, content),
                    org_id=org_id,
                    document_id=document_id,
                    section_id=node.id,
                    ordinal=ordinal,
                    content=content,
                    token_count=max(len(content) // 4, 1),
                    char_start=start,
                    char_end=end,
                )
            )
            ordinal += 1
    return sections, chunks


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

    async def run_structuring_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        object_store: ObjectStore,
    ) -> DocumentOut:
        """STRUCTURING -> EMBEDDING, or -> FAILED with failed_stage=STRUCTURING.

        Idempotent and resumable, same shape as ``run_parsing_stage``: a document already
        past structuring (EMBEDDING or later) is returned unchanged; a document stuck in
        STRUCTURING or previously FAILED at this stage is rebuilt from scratch — its
        existing sections/chunks are deleted and replaced inside one transaction before the
        status advances, so a re-run never leaves duplicates or a partial tree.
        """
        document = await documents_service.begin_structuring(ctx, document_id)
        if document.status != DocumentStatus.STRUCTURING:
            return document

        try:
            artifact_key = await documents_service.get_parse_artifact_key(ctx, document_id)
            raw = await object_store.get(artifact_key)
            artifact = json.loads(raw)
            sections, chunks = _build_sections_and_chunks(
                ctx.org_id,
                document_id,
                artifact["text"],
                artifact["outline"],
                artifact["page_count"],
            )
        except Exception as exc:  # artifact fetch/decode — record, never swallow
            logger.warning(
                "ingestion.structuring_failed",
                document_id=str(document_id),
                org_id=str(ctx.org_id),
                error=str(exc),
            )
            return await documents_service.fail_stage(
                ctx,
                document_id,
                failed_stage=DocumentStatus.STRUCTURING.value,
                error_detail=str(exc),
            )

        async with db_mod.sessionmaker() as session, session.begin():
            section_repo = SectionRepository(session, ctx)
            chunk_repo = ChunkRepository(session, ctx)
            await chunk_repo.delete_for_document(document_id)
            await section_repo.delete_for_document(document_id)
            await section_repo.bulk_create(sections)
            await chunk_repo.bulk_create(chunks)

        return await documents_service.complete_structuring(ctx, document_id)

    async def run_embedding_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        embedder: Embedder,
    ) -> DocumentOut:
        """EMBEDDING -> READY, or -> FAILED with failed_stage=EMBEDDING.

        Idempotent and resumable, same shape as the earlier stages: a document already past
        embedding (READY) is returned unchanged; a document stuck in EMBEDDING or previously
        FAILED at this stage is re-embedded from scratch. Unlike structuring, re-running this
        stage does NOT delete-then-rebuild — it upserts on the embeddings table's
        ``unique(owner_type, owner_id, model)`` constraint, so a re-embed of an unchanged
        chunk under the same model updates the same row instead of inserting a duplicate.
        """
        document = await documents_service.begin_embedding(ctx, document_id)
        if document.status != DocumentStatus.EMBEDDING:
            return document

        try:
            async with db_mod.sessionmaker() as session:
                chunks = await ChunkRepository(session, ctx).list_for_document(document_id)
            vectors = await embedder.embed([chunk.content for chunk in chunks])
        except Exception as exc:  # the only seam call here — record, never swallow
            logger.warning(
                "ingestion.embedding_failed",
                document_id=str(document_id),
                org_id=str(ctx.org_id),
                error=str(exc),
            )
            return await documents_service.fail_stage(
                ctx,
                document_id,
                failed_stage=DocumentStatus.EMBEDDING.value,
                error_detail=str(exc),
            )

        rows = [
            {
                "owner_id": chunk.id,
                "model": embedder.model,
                "dim": embedder.dim,
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with db_mod.sessionmaker() as session, session.begin():
            await EmbeddingRepository(session, ctx).upsert_chunk_embeddings(document_id, rows)

        return await documents_service.complete_embedding(ctx, document_id)


ingestion_service = IngestionService()
