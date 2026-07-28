"""The 3 seam Protocols — and only these (architecture.md "The 3 seams")."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.services.seams.types import Message, ParsedDoc

if TYPE_CHECKING:
    # `ChunkHit` lives in `app.models.ingestion`, which itself imports `EMBED_DIM` from
    # THIS module — a real (non-TYPE_CHECKING) import here would be circular. Safe under
    # `from __future__ import annotations` (annotations are lazy strings) since
    # `Protocol.__instancecheck__` only checks method names, never argument types.
    from app.models.ingestion import ChunkHit

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


@runtime_checkable
class Reranker(Protocol):
    """V2 seam (architecture.md "A Reranker seam is added in V2, not now"), gated behind
    `settings.RERANKER_ENABLED` (default False) — `RetrievalService` never calls this at
    all when the gate is off. Reranks a widened candidate pool of chunk hits against the
    raw query text, returning a smaller, reordered top_k with `rerank_score` stamped on
    each returned hit (higher = more relevant). Agnostic to how the candidates were
    sourced (flat or hierarchical chunk hits) — wraps the FINAL chunk-level output as one
    more transformation step, never a competing retrieval strategy."""

    async def rerank(
        self, query: str, candidates: list[ChunkHit], top_k: int
    ) -> list[ChunkHit]: ...
