"""The 3 seam Protocols — and only these (architecture.md "The 3 seams")."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.platform.seams.types import Message, ParsedDoc

# Embedding width. Must match the pgvector `vector(1536)` column; same-dim model swaps are
# free, a different-dim model needs a migration (see memory.md "embedding-dimension asterisk").
EMBED_DIM = 1536


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
    """Stream a completion token-by-token (async-IO rule: streaming, not a blocking call).
    Exposes `model` (mirrors `Embedder.model`) so callers can log/report which model
    produced an answer without reaching past the seam for vendor config."""

    @property
    def model(self) -> str: ...

    def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...
