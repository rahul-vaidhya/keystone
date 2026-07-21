"""`RealEmbedder`/`RealLLM` — an OpenAI-compatible API, kept behind the seam so the vendor
stays swappable. The vendor SDK is imported lazily and the adapter raises
`SeamNotConfigured` when no key/SDK is present, so the fake-only suite needs nothing
installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.config.settings import settings
from app.services.seams.protocols import EMBED_DIM
from app.services.seams.types import Message, SeamNotConfigured, SeamTransientError


def _classify_transient(exc: Exception) -> bool:
    """True if `exc` (raised by the OpenAI SDK) signals a TRANSIENT failure — timeout,
    connection error, HTTP 429, or HTTP 5xx — the only cases a retry loop above the seam
    should ever retry. Anything else (bad request, auth failure, a bug) returns False so
    it propagates immediately instead of being silently retried."""
    try:
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
    except ImportError:  # pragma: no cover - only reachable if openai is somehow absent
        pass
    else:
        transient_types = (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
        )
        if isinstance(exc, transient_types):
            return True
    status_code = getattr(exc, "status_code", None)
    return status_code is not None and (status_code == 429 or status_code >= 500)


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

    @property
    def model(self) -> str:
        return settings.LLM_MODEL

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        client = _openai_client()
        try:
            stream = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            if _classify_transient(exc):
                raise SeamTransientError(str(exc)) from exc
            raise
