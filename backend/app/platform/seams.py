"""The 3 seams — and only these — that wrap external services.

Per architecture.md "The 3 seams (and only these)": every call to a document parser,
an embeddings API, or an LLM goes through one of these `Protocol`s. Nothing else is a
seam (pgvector, the object store, and the queue are called directly — we are not swapping
Postgres). A `Reranker` arrives in V2, not now.

Two implementations of each:

* **Fakes** — the default everywhere in dev/CI/tests. Deterministic, no API keys, no cost,
  reproducible. `FakeEmbedder` derives its vector from `sha256(text)`; `FakeLLM` streams a
  templated grounded answer citing the context; `FakeParser` returns a fixed text + outline.
* **Real** — the production path, selected **per seam** (`PARSER_MODE`/`EMBEDDER_MODE`/
  `LLM_MODE`, decided F23 — replaces the old single `SEAMS_MODE` switch).
  - `RealEmbedder`/`RealLLM` call an OpenAI-compatible API (the documented default models,
    kept behind the seam so they stay swappable). The vendor SDK is imported lazily and the
    adapter raises `SeamNotConfigured` when no key/SDK is present — so the fake-only suite
    needs nothing installed.
  - `RealParser` (F23) calls OpenRouter's file-parser plugin directly over HTTP — a
    SEPARATE adapter/vendor call from `RealEmbedder`/`RealLLM` above, even though both
    happen to be OpenRouter-compatible endpoints. PDF only; DOCX is a future adapter
    branch. Engine routing: try the free `cloudflare-ai` text engine first, fall back to
    billed `mistral-ocr` only if the text engine's output is negligible (scanned/image PDF).
    Heading structure is recovered from the provider's markdown output, not fabricated —
    see `_parse_markdown_outline`.

Construct seams through the `get_parser` / `get_embedder` / `get_llm` factories (they read
the per-seam mode settings); features inject the returned object so a test can pass a fake.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.platform.config import settings
from app.platform.logging import get_logger

logger = get_logger(__name__)

# Embedding width. Must match the pgvector `vector(1536)` column; same-dim model swaps are
# free, a different-dim model needs a migration (see memory.md "embedding-dimension asterisk").
EMBED_DIM = 1536


class SeamNotConfigured(RuntimeError):
    """A real seam was selected but its vendor/credentials are not wired up.

    Raised by the real adapters (never the fakes), so an accidental ``SEAMS_MODE=real``
    without keys fails loudly instead of silently degrading.
    """


# --------------------------------------------------------------------------------------
# Shared types crossing the seam boundary
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutlineNode:
    """One heading in a parsed document's outline, with structural offsets only.

    The structuring stage (F21) turns these into the `sections` tree — char offsets give
    provenance; page/level support hierarchy later. Semantic fields (summary/topics) are
    NOT here: they are V2 enrichment, populated by a backfill job behind a flag.
    """

    heading: str
    level: int
    char_start: int
    char_end: int
    page_start: int = 1
    page_end: int = 1


@dataclass(frozen=True, slots=True)
class ParsedDoc:
    """What the `Parser` seam returns: raw text + outline + page count + detected language.

    `language` is returned by the parser (it detects it) per the foundation-review L2 fix.
    """

    text: str
    outline: list[OutlineNode]
    language: str
    page_count: int


@dataclass(frozen=True, slots=True)
class Message:
    """A chat turn handed to the `LLM` seam. `role` is system|user|assistant."""

    role: str
    content: str


# --------------------------------------------------------------------------------------
# The seam Protocols
# --------------------------------------------------------------------------------------


@runtime_checkable
class Parser(Protocol):
    """Extract text + structure from a document blob. OCR stays behind this seam."""

    async def extract(self, blob: bytes, mime: str) -> ParsedDoc: ...


@runtime_checkable
class Embedder(Protocol):
    """Turn texts into vectors. Exposes `model` (stamped onto `embeddings.model`, the
    retrieval filter that prevents duplicate hits after a re-embed) and `dim`."""

    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class LLM(Protocol):
    """Stream a completion token-by-token (async-IO rule: streaming, not a blocking call)."""

    def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...


# --------------------------------------------------------------------------------------
# Fakes — the default in dev/CI/tests
# --------------------------------------------------------------------------------------

_FAKE_TEXT = (
    "Introduction\n"
    "This is a fake parsed document used in tests and local development.\n\n"
    "Background\n"
    "It returns a fixed outline so the structuring stage can run without a real PDF.\n"
)


class FakeParser:
    """Returns a fixed text + two-heading outline with valid char offsets — deterministic,
    so structuring tests assert against a known shape without a real parser."""

    async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
        text = _FAKE_TEXT
        bg = text.index("Background")
        outline = [
            OutlineNode(heading="Introduction", level=1, char_start=0, char_end=bg),
            OutlineNode(heading="Background", level=1, char_start=bg, char_end=len(text)),
        ]
        return ParsedDoc(text=text, outline=outline, language="en", page_count=1)


class FakeEmbedder:
    """Deterministic unit vector derived from ``sha256(text)`` → reproducible retrieval
    assertions. Same text → same vector; different text → different vector."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim

    @property
    def model(self) -> str:
        return f"fake-embed-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")
        rng = random.Random(seed)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(self._dim)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class FakeLLM:
    """Streams a templated grounded answer that cites the provided context (``[1]``) — so
    citation-mapping tests have a stable, source-referencing output."""

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        question = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        ).strip()
        answer = f"Based on the provided sources, here is the answer to: {question[:80]} [1]"
        for token in answer.split(" "):
            yield token + " "


# --------------------------------------------------------------------------------------
# Real adapters — the production path (SEAMS_MODE=real)
# --------------------------------------------------------------------------------------


def _openai_client():
    """Lazily build an OpenAI-compatible async client, or raise `SeamNotConfigured`.

    Imported lazily and gated on config so the default (fake) install needs neither the
    `openai` package nor any API key.
    """
    if not settings.OPENAI_API_KEY:
        raise SeamNotConfigured(
            "OPENAI_API_KEY is not set; the real Embedder/LLM seams require it. "
            "Use SEAMS_MODE=fake for tests/local."
        )
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - exercised only on the real path
        raise SeamNotConfigured(
            "the 'openai' package is not installed; install veratas-backend[real] "
            "to use SEAMS_MODE=real."
        ) from exc
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL)


class RealEmbedder:
    """OpenAI-compatible embeddings (default `text-embedding-3-small`, 1536-d → vector(1536))."""

    def __init__(self) -> None:
        self._model = settings.EMBEDDING_MODEL

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return EMBED_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = _openai_client()
        resp = await client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in resp.data]


class RealLLM:
    """OpenAI-compatible streaming chat completion (default mini-class `LLM_MODEL`)."""

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        client = _openai_client()
        stream = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


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


# --------------------------------------------------------------------------------------
# Factories — one independent switch per seam (decided F23)
# --------------------------------------------------------------------------------------

_VALID_MODES = ("fake", "real")


def _resolve_mode(mode: str, setting_name: str) -> str:
    if mode not in _VALID_MODES:
        raise SeamNotConfigured(f"unknown {setting_name}={mode!r}; expected one of {_VALID_MODES}.")
    return mode


def get_parser() -> Parser:
    mode = _resolve_mode(settings.PARSER_MODE, "PARSER_MODE")
    return FakeParser() if mode == "fake" else RealParser()


def get_embedder() -> Embedder:
    mode = _resolve_mode(settings.EMBEDDER_MODE, "EMBEDDER_MODE")
    return FakeEmbedder() if mode == "fake" else RealEmbedder()


def get_llm() -> LLM:
    mode = _resolve_mode(settings.LLM_MODE, "LLM_MODE")
    return FakeLLM() if mode == "fake" else RealLLM()
