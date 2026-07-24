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
    """Sliding-window-COUNTER rate limiter, keyed on
    ``f"ratelimit:{key}:{window}"`` where ``window`` is a window-seconds-wide time
    bucket. Blends the current window's count with the immediately-previous window's
    count, weighted by how far into the current window "now" is — this is what avoids
    a PLAIN fixed-window counter's well-known boundary flaw: a client could otherwise
    send a full quota in the last instant of one window and a full quota again in the
    first instant of the next, briefly bursting to ~2x the configured limit. O(1)
    memory per key (two windows), no new dependency — the standard technique used by
    e.g. Cloudflare/Kong's public rate-limiting write-ups, chosen over a token bucket
    or a full request log to keep this a small, self-contained change.

    ``INCR`` then ``EXPIRE`` ONLY when the incremented count is exactly 1 (the first
    hit in this window). The expiry is 2 windows wide (not 1) so THIS window's key is
    still readable as "the previous window" once the next window begins — letting it
    expire naturally after that is simpler than deleting it manually."""

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client

    async def _get_redis(self) -> object:
        if self._redis is None:
            self._redis = await _get_shared_redis()
        return self._redis

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        current_window = int(now // window_seconds)
        previous_window = current_window - 1
        redis_client = await self._get_redis()

        current_key = f"ratelimit:{key}:{current_window}"
        current_count = await redis_client.incr(current_key)  # type: ignore[attr-defined]
        if current_count == 1:
            await redis_client.expire(current_key, window_seconds * 2)  # type: ignore[attr-defined]

        previous_raw = await redis_client.get(f"ratelimit:{key}:{previous_window}")  # type: ignore[attr-defined]
        previous_count = int(previous_raw) if previous_raw is not None else 0

        elapsed_fraction = (now % window_seconds) / window_seconds
        weighted_count = previous_count * (1 - elapsed_fraction) + current_count
        return weighted_count <= limit


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency factory. Tests override this via
    ``app.dependency_overrides`` with an in-memory fake instead of constructing a real
    Redis client."""
    return RedisRateLimiter()
