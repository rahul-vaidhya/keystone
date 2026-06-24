"""Fakes — the default seam implementations in dev/CI/tests.

Deterministic, no API keys, no cost, reproducible. `FakeEmbedder` derives its vector from
`sha256(text)`; `FakeLLM` streams a templated grounded answer citing the context;
`FakeParser` returns a fixed text + outline.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import AsyncIterator

from app.platform.seams.protocols import EMBED_DIM
from app.platform.seams.types import Message, OutlineNode, ParsedDoc

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
    """Streams a templated answer that is context-aware in the one way F40's grounding
    contract requires: if the prompt carries no numbered context block (no ``[1]``
    anywhere in the latest user turn), it refuses with the same fixed string F40's system
    prompt instructs a real model to use; otherwise it cites the provided context
    (``[1]``). Deterministic, no network — lets tests assert refusal-SHAPED output on
    empty context, not just plumbing."""

    REFUSAL = "I don't have that in the provided sources."

    @property
    def model(self) -> str:
        return "fake-llm"

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        if "[1]" not in user_content:
            answer = self.REFUSAL
        else:
            question = user_content.rsplit("Question:", 1)[-1].strip()
            answer = f"Based on the provided sources, here is the answer to: {question[:80]} [1]"
        for token in answer.split(" "):
            yield token + " "
