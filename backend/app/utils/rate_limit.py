"""Fixed-window rate limiting via Redis — used only by the embed widget's PUBLIC
(unauthenticated) chat endpoint (docs/embed-widget-plan.md). Nothing else in this app
has needed rate limiting before this feature.

Same DI/config-selected treatment as ``ObjectStore``/``JobQueue`` (``app/services/
storage.py``/``app/services/queue.py``) — NOT one of the 3 seams (Parser/Embedder/LLM):
this isn't a call to an external AI vendor, it's a network-facing abuse guard. Tests
override the ``get_rate_limiter`` FastAPI dependency with an in-memory fake, so the
offline suite never needs a real Redis.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from app.config.settings import settings


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """Records one hit for ``key`` and returns ``True`` if it is allowed under
        ``limit`` within the current fixed window, ``False`` if the caller should be
        rejected."""
        ...


_shared_redis: object | None = None
_shared_redis_lock = asyncio.Lock()


async def _get_shared_redis() -> object:
    """One process-wide Redis client for the HTTP path (mirrors ``services/queue.py``'s
    ``_get_shared_pool`` pattern) — without this, every rate-limited request would open
    its own Redis connection and never close it."""
    global _shared_redis
    if _shared_redis is None:
        async with _shared_redis_lock:
            if _shared_redis is None:
                import redis.asyncio as aioredis

                _shared_redis = aioredis.from_url(settings.REDIS_URL)
    return _shared_redis


class RedisRateLimiter:
    """Fixed-window counter keyed on ``f"ratelimit:{key}:{window}"`` where ``window``
    is the current window-seconds-wide time bucket. ``INCR`` then ``EXPIRE`` ONLY when
    the incremented count is exactly 1 (the first hit in this window) — expiring on
    every hit would silently extend the window indefinitely under sustained traffic."""

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client

    async def _get_redis(self) -> object:
        if self._redis is None:
            self._redis = await _get_shared_redis()
        return self._redis

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        window = int(time.time() // window_seconds)
        redis_client = await self._get_redis()
        full_key = f"ratelimit:{key}:{window}"
        count = await redis_client.incr(full_key)  # type: ignore[attr-defined]
        if count == 1:
            await redis_client.expire(full_key, window_seconds)  # type: ignore[attr-defined]
        return count <= limit


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency factory. Tests override this via
    ``app.dependency_overrides`` with an in-memory fake instead of constructing a real
    Redis client."""
    return RedisRateLimiter()
