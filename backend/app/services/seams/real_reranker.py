"""`RealReranker` (V2) — an HTTP client for a self-hosted BGE-reranker-v2-m3 instance,
served locally via Hugging Face Text-Embeddings-Inference (TEI)'s `/rerank` endpoint (see
the `reranker` service in docker-compose.yml, only needed locally when
`RERANKER_MODE=real`). An HTTP client like `real_parser.py`'s OpenRouter file-parser call
— NOT an SDK wrapper like `real_llm.py`'s `RealEmbedder`/`RealLLM` — so `httpx` is
lazy-imported the same way `real_parser.py` does, even though it's already a core
dependency, to keep the lazy-import convention consistent across every real adapter in
this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.logging import get_logger
from app.config.settings import settings
from app.services.seams.types import SeamNotConfigured, SeamTransientError

if TYPE_CHECKING:
    # `ChunkHit` lives in `app.models.ingestion`, which itself imports `EMBED_DIM` from
    # `app.services.seams.protocols` — a real (non-TYPE_CHECKING) import here is
    # circular whenever `app.models.ingestion` is the entry point (it is, in practice:
    # `app/models/__init__.py` imports it before `app.services.seams` finishes loading).
    # Safe under `from __future__ import annotations` since this module never
    # instantiates `ChunkHit` directly — it only calls `.model_copy()` on instances
    # already handed to it.
    from app.models.ingestion import ChunkHit

logger = get_logger(__name__)


def _classify_transient(exc: Exception) -> bool:
    """True if `exc` (raised by httpx) signals a TRANSIENT failure — timeout, connection
    error, HTTP 429, or HTTP 5xx — the only cases a retry loop above the seam should ever
    retry. Mirrors `real_llm.py`'s `_classify_transient`, adapted for httpx's exception
    types instead of the openai SDK's. Anything else (a bug, a 4xx validation failure)
    returns False so it propagates immediately instead of being silently retried."""
    import httpx

    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


async def _call_tei_rerank(query: str, texts: list[str]) -> list[dict]:
    """One POST to TEI's `/rerank` endpoint. Returns the raw `[{"index": int, "score":
    float}, ...]` list — NOT guaranteed sorted by this function; `RealReranker.rerank`
    sorts explicitly rather than trusting the vendor's ordering."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.RERANKER_URL}/rerank",
            json={"query": query, "texts": texts, "raw_scores": False},
        )
        response.raise_for_status()
        return response.json()


class RealReranker:
    """Self-hosted BGE-reranker-v2-m3 via Hugging Face TEI's `/rerank` endpoint. Raises
    `SeamNotConfigured` when `RERANKER_URL` is unset — mirrors `real_llm.py`'s
    `_openai_client()` gating pattern."""

    async def rerank(self, query: str, candidates: list[ChunkHit], top_k: int) -> list[ChunkHit]:
        if not candidates:
            return []
        if not settings.RERANKER_URL:
            raise SeamNotConfigured(
                "RERANKER_URL is not set; the real Reranker seam requires it. "
                "Use RERANKER_MODE=fake for tests/local, or run the local TEI reranker "
                "service (docker-compose reranker)."
            )

        try:
            results = await _call_tei_rerank(query, [hit.content for hit in candidates])
        except Exception as exc:
            if _classify_transient(exc):
                raise SeamTransientError(str(exc)) from exc
            raise

        ranked = sorted(results, key=lambda r: r["score"], reverse=True)[:top_k]
        reranked_hits = [
            candidates[r["index"]].model_copy(update={"rerank_score": r["score"]}) for r in ranked
        ]
        logger.info(
            "seams.real_reranker_reranked",
            candidate_count=len(candidates),
            returned_count=len(reranked_hits),
        )
        return reranked_hits
