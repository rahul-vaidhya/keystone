"""`RealParser` (F23) — OpenRouter's file-parser plugin, called directly over HTTP. PDF
only — DOCX is a future adapter branch. A SEPARATE adapter/vendor call from
`RealEmbedder`/`RealLLM` (see real_llm.py), even though both happen to be
OpenRouter-compatible endpoints.

Engine routing: the PDF's own text layer via local pypdf first (lossless, free, real page
numbers — see `_extract_text_locally`); only a PDF with no usable text layer goes to the
remote engines: the free `cloudflare-ai` engine, then billed `mistral-ocr` if its output
is negligible (a scanned/image PDF). If both
engines yield negligible text, raises so the stage fails cleanly rather than persisting
garbage. Heading structure is recovered from the provider's markdown output, never
fabricated — see `_parse_markdown_outline`.
"""

from __future__ import annotations

import base64
import io
import re

from app.config.logging import get_logger
from app.config.settings import settings
from app.services.seams.types import OutlineNode, ParsedDoc, SeamNotConfigured

logger = get_logger(__name__)

# Markdown heading line, e.g. "## Background" → level=2, heading="Background". Both
# `cloudflare-ai` and `mistral-ocr` return markdown, so this is how real heading structure
# (if the provider returned any) is recovered — never fabricated.
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _pdf_page_count(blob: bytes) -> int:
    """Reads the page count locally (never trusted from the API response) — also the
    first point an encrypted PDF is detected and rejected, before any network call."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    if reader.is_encrypted:
        raise ValueError("RealParser: encrypted PDFs are not supported.")
    return len(reader.pages)


def _extract_text_locally(blob: bytes) -> tuple[str, list[OutlineNode], int]:
    """Local pypdf text layer, emitted in the same ``### Page N`` marker shape the remote
    engine uses (citation page derivation keys off those markers). Tried first because
    `cloudflare-ai` was found to silently drop bold/italic spans — in a textbook those
    are exactly the defined key terms (e.g. "Ekman transport" vanished from the parsed
    text, so questions about it were refused). Each page becomes one outline node with
    its REAL page number. Returns (text, outline, body_chars) — body_chars excludes the
    markers so the negligible-text check still detects scanned PDFs."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    text = ""
    outline: list[OutlineNode] = []
    body_chars = 0
    for number, page in enumerate(reader.pages, start=1):
        body = (page.extract_text() or "").strip()
        body_chars += len(body)
        start = len(text)
        text += f"### Page {number}\n{body}\n\n"
        outline.append(
            OutlineNode(
                heading=f"Page {number}",
                level=3,
                char_start=start,
                char_end=len(text),
                page_start=number,
                page_end=number,
            )
        )
    return text, outline, body_chars


def _is_negligible_text(text: str, page_count: int) -> bool:
    """True when an engine's output is too sparse to be real extracted text (signals a
    scanned/image PDF that needs the OCR engine instead of the free text engine)."""
    chars_per_page = len(text.strip()) / max(page_count, 1)
    return chars_per_page < settings.PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE


async def _call_openrouter_file_parser(blob: bytes, engine: str) -> str:
    """One OpenRouter chat/completions call with the file-parser plugin enabled for the
    given PDF engine. The model's generated text is discarded (`max_tokens=1`) — only the
    plugin's file annotations (the actual parsed content) are read."""
    import httpx

    encoded = base64.b64encode(blob).decode("ascii")
    payload = {
        "model": settings.PARSER_MODEL,
        "temperature": 0,
        "max_tokens": 1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Parse this document."},
                    {
                        "type": "file",
                        "file": {
                            "filename": "document.pdf",
                            "file_data": f"data:application/pdf;base64,{encoded}",
                        },
                    },
                ],
            }
        ],
        "plugins": [{"id": "file-parser", "pdf": {"engine": engine}}],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    annotations = data["choices"][0]["message"].get("annotations") or []
    parts: list[str] = []
    for annotation in annotations:
        if annotation.get("type") != "file":
            continue
        for block in annotation.get("file", {}).get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
    return "\n".join(parts)


def _parse_markdown_outline(text: str, page_count: int) -> list[OutlineNode]:
    """Recovers heading structure from the provider's markdown output, in document order.

    Each heading's `char_end` is the start of the next heading at the SAME OR SHALLOWER
    level (its next sibling, or its parent's next sibling) — not just the next heading in
    the flat list — so a parent's range still covers its children. An empty result (no
    `#`-style headings in the output) is a valid, expected finding for some documents —
    it is passed through flat, exercising F21's degenerate-outline contract, never
    fabricated.

    Page provenance is NOT recoverable from markdown output (no page-boundary markers), so
    every node gets the document-level `page_start=1, page_end=page_count` rather than a
    real per-heading page span — a known F23 finding, not a bug.
    """
    matches = list(_MARKDOWN_HEADING_RE.finditer(text))
    nodes: list[OutlineNode] = []
    for i, match in enumerate(matches):
        level = len(match.group(1))
        char_end = len(text)
        for later in matches[i + 1 :]:
            if len(later.group(1)) <= level:
                char_end = later.start()
                break
        nodes.append(
            OutlineNode(
                heading=match.group(2).strip(),
                level=level,
                char_start=match.start(),
                char_end=char_end,
                page_start=1,
                page_end=max(page_count, 1),
            )
        )
    return nodes


class RealParser:
    """OpenRouter file-parser plugin (F23). PDF only — DOCX is a future adapter branch.

    Engine routing minimizes OCR cost: try the free `cloudflare-ai` text engine first;
    only retry with billed `mistral-ocr` if its output is negligible (a scanned/image
    PDF). If both engines yield negligible text, raises so the stage fails cleanly rather
    than persisting garbage.
    """

    async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
        if mime != "application/pdf":
            raise ValueError(
                f"RealParser supports PDF only, got mime={mime!r}; DOCX is a future "
                "adapter branch, not built in F23."
            )
        if not settings.OPENROUTER_API_KEY:
            raise SeamNotConfigured(
                "OPENROUTER_API_KEY is not set; the real Parser seam requires it. "
                "Use PARSER_MODE=fake for tests/local."
            )

        page_count = _pdf_page_count(blob)

        text, outline, body_chars = _extract_text_locally(blob)
        if not _is_negligible_text("x" * body_chars, page_count):
            logger.info(
                "seams.real_parser_extracted",
                engine="pypdf",
                page_count=page_count,
                chars=len(text),
                headings_recovered=False,
            )
            return ParsedDoc(text=text, outline=outline, language="en", page_count=page_count)

        # No usable text layer (scanned/image PDF) -> remote engines, as before.
        text = await _call_openrouter_file_parser(blob, engine="cloudflare-ai")
        engine = "cloudflare-ai"
        if _is_negligible_text(text, page_count):
            text = await _call_openrouter_file_parser(blob, engine="mistral-ocr")
            engine = "mistral-ocr"
            if _is_negligible_text(text, page_count):
                raise RuntimeError(
                    "RealParser: both cloudflare-ai and mistral-ocr returned negligible "
                    f"text ({len(text.strip())} chars over {page_count} pages) — likely "
                    "an empty, corrupt, or unsupported PDF."
                )

        outline = _parse_markdown_outline(text, page_count)
        logger.info(
            "seams.real_parser_extracted",
            engine=engine,
            page_count=page_count,
            chars=len(text),
            headings_recovered=bool(outline),
        )
        return ParsedDoc(text=text, outline=outline, language="en", page_count=page_count)
