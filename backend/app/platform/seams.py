"""The 3 seams — and only these — that wrap external services.

Per architecture.md "The 3 seams (and only these)": every call to a document parser,
an embeddings API, or an LLM goes through one of these `Protocol`s. Nothing else is a
seam (pgvector, the object store, and the queue are called directly — we are not swapping
Postgres). A `Reranker` arrives in V2, not now.

Two implementations of each:

* **Fakes** — the default everywhere in dev/CI/tests. Deterministic, no API keys, no cost,
  reproducible. `FakeEmbedder` derives its vector from `sha256(text)`; `FakeLLM` streams a
  templated grounded answer citing the context; `FakeParser` returns a fixed text + outline.
* **Real** — the production path, selected by `SEAMS_MODE=real`.
  - `RealEmbedder`/`RealLLM` call an OpenAI-compatible API (the documented default models,
    kept behind the seam so they stay swappable). The vendor SDK is imported lazily and the
    adapter raises `SeamNotConfigured` when no key/SDK is present — so the fake-only suite
    needs nothing installed.
  - `RealParser` is a **Phase-2 (F20) stub**: the parser/OCR vendor is a deliberate deferral
    (see memory.md / librarydocs.md), so it raises `SeamNotConfigured` until that choice lands.

Construct seams through the `get_parser` / `get_embedder` / `get_llm` factories (they read
`SEAMS_MODE`); features inject the returned object so a test can pass a fake.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.platform.config import settings

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


class RealParser:
    """Phase-2 (F20) stub. The parser/OCR vendor is a deliberate deferral — see memory.md
    and librarydocs.md ("Decided in Phase 2"). Until then the real path is unconfigured."""

    async def extract(self, blob: bytes, mime: str) -> ParsedDoc:
        raise SeamNotConfigured(
            "the production document parser/OCR vendor is selected in Phase 2 (F20); "
            "use SEAMS_MODE=fake until then."
        )


# --------------------------------------------------------------------------------------
# Factories — the single switch on SEAMS_MODE
# --------------------------------------------------------------------------------------

_VALID_MODES = ("fake", "real")


def _check_mode() -> str:
    mode = settings.SEAMS_MODE
    if mode not in _VALID_MODES:
        raise SeamNotConfigured(f"unknown SEAMS_MODE={mode!r}; expected one of {_VALID_MODES}.")
    return mode


def get_parser() -> Parser:
    return FakeParser() if _check_mode() == "fake" else RealParser()


def get_embedder() -> Embedder:
    return FakeEmbedder() if _check_mode() == "fake" else RealEmbedder()


def get_llm() -> LLM:
    return FakeLLM() if _check_mode() == "fake" else RealLLM()
