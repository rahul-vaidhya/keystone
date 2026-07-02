"""F21 structuring stage: build the ``sections`` tree (structural only) from the parser's
flat outline + ``chunks`` with offsets + ``section_id``. See architecture.md "Ingestion
pipeline" and its degenerate-outline contract."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from app.config import db as db_mod
from app.config.logging import get_logger
from app.middleware.context import TenantContext
from app.models.documents import DocumentOut, DocumentStatus
from app.models.ingestion import Chunk, Section
from app.services.documents import documents_service
from app.services.ingestion.repository import ChunkRepository, SectionRepository
from app.services.storage import ObjectStore

logger = get_logger(__name__)

# Target chunk window, in characters — no tokenizer dependency yet, so token_count is a
# rough heuristic (len(content) // 4), not an exact count.
CHUNK_TARGET_CHARS = 1000


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


async def run_structuring_stage(
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
