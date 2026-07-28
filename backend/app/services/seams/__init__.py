"""The 3 seams — and a 4th, V2-only `Reranker` — that wrap external services.

Per architecture.md "The 3 seams (and only these)": every call to a document parser,
an embeddings API, or an LLM goes through one of these `Protocol`s. Nothing else is a
seam (pgvector, the object store, and the queue are called directly — we are not swapping
Postgres). The `Reranker` seam is the V2 addition architecture.md names ("A `Reranker`
seam is added in V2, not now") — gated behind `settings.RERANKER_ENABLED` (default
`False`), so its mere existence changes nothing until explicitly turned on.

Two implementations of each:

* **Fakes** — the default everywhere in dev/CI/tests. Deterministic, no API keys, no cost,
  reproducible. `FakeEmbedder` derives its vector from `sha256(text)`; `FakeLLM` streams a
  templated grounded answer citing the context; `FakeParser` returns a fixed text + outline;
  `FakeReranker` is a true identity passthrough that stamps `rerank_score = 1.0 - distance`.
* **Real** — the production path, selected **per seam** (`PARSER_MODE`/`EMBEDDER_MODE`/
  `LLM_MODE`/`RERANKER_MODE`, decided F23 for the first 3 — replaces the old single
  `SEAMS_MODE` switch).
  - `RealEmbedder`/`RealLLM` call an OpenAI-compatible API (the documented default models,
    kept behind the seam so they stay swappable). The vendor SDK is imported lazily and the
    adapter raises `SeamNotConfigured` when no key/SDK is present — so the fake-only suite
    needs nothing installed.
  - `RealParser` (F23) calls OpenRouter's file-parser plugin directly over HTTP — a
    SEPARATE adapter/vendor call from `RealEmbedder`/`RealLLM` above, even though both
    happen to be OpenRouter-compatible endpoints. PDF only; DOCX is a future adapter
    branch. Engine routing: try the free `cloudflare-ai` text engine first, fall back to
    billed `mistral-ocr` only if the text engine's output is negligible (scanned/image
    PDF). Heading structure is recovered from the provider's markdown output, not
    fabricated — see `_parse_markdown_outline`.
  - `RealReranker` calls a self-hosted BGE-reranker-v2-m3 instance directly over HTTP via
    Hugging Face Text-Embeddings-Inference's `/rerank` endpoint — another HTTP-client
    adapter like `RealParser`, not an SDK wrapper like `RealEmbedder`/`RealLLM`.

Construct seams through the `get_parser` / `get_embedder` / `get_llm` / `get_reranker`
factories (they read the per-seam mode settings); features inject the returned object so
a test can pass a fake.

This package is a structural split of what used to be one flat `seams.py` (the refactor
that introduced this split made ZERO logic changes — see memory.md). Every name below is
re-exported here so existing import paths (``from app.services.seams import X``) keep
resolving unchanged, including the two private helpers (`_is_negligible_text`,
`_parse_markdown_outline`) that `tests/test_seams.py` imports directly to unit-test
`RealParser`'s pure-logic pieces without a network call.
"""

from __future__ import annotations

from app.services.seams.factory import get_embedder, get_llm, get_parser, get_reranker
from app.services.seams.fakes import FakeEmbedder, FakeLLM, FakeParser, FakeReranker
from app.services.seams.protocols import EMBED_DIM, LLM, Embedder, Parser, Reranker
from app.services.seams.real_llm import RealEmbedder, RealLLM
from app.services.seams.real_parser import (
    RealParser,
    _is_negligible_text,
    _parse_markdown_outline,
)
from app.services.seams.real_reranker import RealReranker
from app.services.seams.types import (
    Message,
    OutlineNode,
    ParsedDoc,
    SeamNotConfigured,
    SeamTransientError,
)

__all__ = [
    "EMBED_DIM",
    "LLM",
    "Embedder",
    "FakeEmbedder",
    "FakeLLM",
    "FakeParser",
    "FakeReranker",
    "Message",
    "OutlineNode",
    "ParsedDoc",
    "Parser",
    "RealEmbedder",
    "RealLLM",
    "RealParser",
    "RealReranker",
    "Reranker",
    "SeamNotConfigured",
    "SeamTransientError",
    "_is_negligible_text",
    "_parse_markdown_outline",
    "get_embedder",
    "get_llm",
    "get_parser",
    "get_reranker",
]
