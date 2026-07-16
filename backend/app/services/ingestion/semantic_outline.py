"""Semantic outline post-pass for degenerate parser outlines.

When a parser outline carries no real semantic structure (e.g., cloudflare-ai on a
two-column PDF emits only page-level wrappers like "document.pdf > Metadata > Contents >
Page N"), this flag-gated LLM post-pass recovers real headings from the extracted text.

Hard rule: offsets are located by exact string search in the raw text, never invented.
Any failure degrades to the parser outline — this stage never fails because of the
semantic pass. The post-pass is idempotent: the derived outline is cached in object
storage alongside the parser artifact.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.config.logging import get_logger
from app.config.settings import settings
from app.services.seams import Message

if TYPE_CHECKING:
    from app.services.seams import LLM

logger = get_logger(__name__)

_PAGE_HEADING_RE = re.compile(r"^page\s+\d+$", re.IGNORECASE)
_GENERIC_HEADINGS = {"metadata", "contents"}
_FILENAME_RE = re.compile(r"\.\w{2,5}$")


def _is_generic_heading(heading: str) -> bool:
    """True for headings that carry no semantic structure: empty strings, page markers
    ("Page N"), generic wrappers ("Metadata"/"Contents"), and filenames ("document.pdf").
    Used both to decide whether a parser outline is degenerate AND to filter junk headings
    the LLM proposes (the same literal strings appear in the extracted text, so the LLM
    can legitimately quote them — but they are structure noise, not sections)."""
    stripped = heading.strip()
    if not stripped:
        return True
    if _PAGE_HEADING_RE.match(stripped):
        return True
    if stripped.lower() in _GENERIC_HEADINGS:
        return True
    if _FILENAME_RE.search(stripped):
        return True
    return False


# Overlap between consecutive LLM windows so a heading straddling a window boundary is
# fully contained in at least one window (it would otherwise be truncated in BOTH windows
# and silently lost — the "never fabricate" contract means we can't recover it). Capped at
# a quarter of the window so tiny test windows still make forward progress.
_WINDOW_OVERLAP_CHARS = 500

_SYSTEM_PROMPT = (
    "You extract the section-heading outline of a document from its raw extracted text. "
    "Respond with ONLY a JSON array, no prose, no code fences."
)

_USER_PROMPT_TEMPLATE = (
    "Identify the real section/chapter headings in the following text segment. "
    "Return a JSON array of objects with the schema: "
    '{{ "heading": string, "level": integer 1-6 }}. '
    "The heading string MUST be quoted EXACTLY as it appears in the text, character-for-character "
    "(it will be located by exact string match). List them in order of appearance. "
    "Return [] if there are none.\n\n"
    "=== TEXT SEGMENT ===\n{segment}"
)


def outline_is_degenerate(outline: list[dict]) -> bool:
    """True when the parser outline carries no real semantic structure.

    Empty outlines, or those containing only page markers (e.g., "Page N"), generic
    wrappers (e.g., "Metadata", "Contents"), or filenames (e.g., "document.pdf") are
    considered degenerate — the exact shape cloudflare-ai emits on PDFs where it
    recovers no headings.
    """
    if not outline:
        return True

    for node in outline:
        heading = (node.get("heading") or "").strip()
        if _is_generic_heading(heading):
            continue
        # Found at least one real heading.
        return False

    # All headings are generic or empty.
    return True


def _parse_headings(raw: str) -> list[tuple[str, int]]:
    """Parse the LLM's JSON response into (heading, level) pairs.

    Handles code-fenced responses (strips leading/trailing ```json fences).
    Requires each item to have a non-empty "heading" string (max 300 chars after strip)
    and an integer "level" clamped to 1..6. Invalid items are skipped.

    Raises json.JSONDecodeError on malformed JSON.
    """
    text = raw.strip()
    # Strip code fences if present.
    if text.startswith("```"):
        # Remove opening fence and optional language tag.
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    elif text.startswith("```json"):
        # Remove ```json specifically.
        text = text[7:]  # len("```json")
        if text.endswith("```"):
            text = text[:-3].rstrip()

    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")

    headings: list[tuple[str, int]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        heading = item.get("heading")
        level = item.get("level")
        if not isinstance(heading, str) or not heading.strip():
            continue
        if not isinstance(level, int):
            continue
        heading_str = heading.strip()
        if len(heading_str) > 300:
            continue
        level_clamped = max(1, min(level, 6))
        headings.append((heading_str, level_clamped))

    return headings


async def derive_semantic_outline(
    text: str,
    page_count: int,
    llm: LLM,
    *,
    window_chars: int | None = None,
) -> list[dict]:
    """Recover real headings from document text using an LLM.

    Splits text into consecutive windows, calls the LLM on each, and locates headings
    by exact string search. Returns a list of outline nodes in the same shape as the
    parser artifact, with char_start/char_end computed per the parser's rules.

    On any failure (LLM error, parse error, heading not found), logs and continues.
    Returns an empty list if no headings are located. On full failure, returns [].
    """
    if window_chars is None:
        window_chars = settings.SEMANTIC_OUTLINE_WINDOW_CHARS

    # Split text into overlapping windows (see _WINDOW_OVERLAP_CHARS).
    overlap = min(_WINDOW_OVERLAP_CHARS, window_chars // 4)
    windows: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + window_chars, len(text))
        windows.append(text[pos:end])
        if end >= len(text):
            break
        pos = end - overlap

    # Collect all (heading, level) pairs in document order, with their window indices.
    all_headings: list[tuple[str, int, int]] = []  # (heading, level, window_idx)

    for window_idx, window in enumerate(windows):
        try:
            messages = [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=_USER_PROMPT_TEMPLATE.format(segment=window)),
            ]
            # Consume the LLM stream to completion.
            completion = "".join([tok async for tok in llm.stream(messages)])
            headings = _parse_headings(completion)
            for heading, level in headings:
                if _is_generic_heading(heading):
                    # Junk the LLM quoted from the text (page markers, wrappers,
                    # filenames). Dropped BEFORE offset location so it never consumes
                    # the cursor or warps a neighbor's char_end.
                    logger.info(
                        "ingestion.semantic_outline_generic_heading_dropped",
                        heading=heading[:80],
                    )
                    continue
                if all_headings:
                    prev_heading, _, prev_window_idx = all_headings[-1]
                    if heading == prev_heading and prev_window_idx == window_idx - 1:
                        # Same heading, re-proposed by the directly-adjacent window:
                        # almost certainly the overlap zone re-detecting the boundary
                        # heading, NOT a genuine repeat (genuine repeats — e.g. the same
                        # "Diet" H2 under two parents — are pages apart, never in two
                        # adjacent windows' shared overlap). Deduping here keeps the
                        # forward find() cursor from attaching a later occurrence.
                        continue
                all_headings.append((heading, level, window_idx))
        except Exception as exc:
            logger.warning(
                "ingestion.semantic_outline_window_failed",
                window_idx=window_idx,
                error=str(exc),
            )
            continue

    # Locate headings in the full text by exact string search with a forward-moving cursor.
    nodes: list[dict] = []
    cursor = 0
    for heading, level, _ in all_headings:
        idx = text.find(heading, cursor)
        if idx == -1:
            logger.warning(
                "ingestion.semantic_outline_heading_not_found",
                heading=heading[:80],
            )
            continue
        char_start = idx
        cursor = idx + len(heading)
        nodes.append(
            {
                "heading": heading,
                "level": level,
                "char_start": char_start,
                "char_end": -1,  # Placeholder; computed below.
                "page_start": 1,
                "page_end": max(page_count, 1),
            }
        )

    # Compute char_end for each node: the char_start of the next node at the same or
    # shallower level, or len(text) if no such node exists.
    for i, node in enumerate(nodes):
        node_level = node["level"]
        node_end = len(text)
        for j in range(i + 1, len(nodes)):
            if nodes[j]["level"] <= node_level:
                node_end = nodes[j]["char_start"]
                break
        node["char_end"] = node_end

    if nodes:
        logger.info("ingestion.semantic_outline_derived", node_count=len(nodes))

    return nodes
