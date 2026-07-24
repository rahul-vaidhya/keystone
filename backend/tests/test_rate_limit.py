"""``RedisRateLimiter`` (app/utils/rate_limit.py) — the sliding-window-counter
algorithm behind the public embed chat endpoint's per-widget/per-IP limits. Pure unit
tests against a minimal in-memory fake Redis client (incr/expire/get), with
``time.time`` monkeypatched for deterministic window-boundary control. No real Redis,
no network — matches this project's CI-offline invariant (real Redis is only ever
available in local dev via docker-compose, never in CI).

Fixes a real gap found in review: a PLAIN fixed-window counter lets a client burst up
to ~2x the configured limit by timing requests around a window edge (send the full
quota in the last instant of one window, then the full quota again in the first
instant of the next — each window's own raw count stays under the limit even though
~2x requests landed within a few milliseconds of each other). These tests pin the
fix directly against that exact scenario.
"""

from __future__ import annotations

from app.utils.rate_limit import RedisRateLimiter


class FakeRedis:
    """In-memory stand-in for the two Redis commands RedisRateLimiter calls. TTLs are
    recorded but never enforced — nothing here needs a key to actually expire to prove
    the counting algorithm itself is correct."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def get(self, key: str) -> bytes | None:
        if key not in self._counters:
            return None
        return str(self._counters[key]).encode()


def _limiter() -> RedisRateLimiter:
    return RedisRateLimiter(redis_client=FakeRedis())


async def test_allows_up_to_the_limit_within_one_window(monkeypatch) -> None:
    limiter = _limiter()
    monkeypatch.setattr("app.utils.rate_limit.time.time", lambda: 1000.0)
    results = [await limiter.hit("k", 5, window_seconds=60) for _ in range(5)]
    assert results == [True] * 5
    assert await limiter.hit("k", 5, window_seconds=60) is False


async def test_boundary_burst_is_capped_unlike_a_plain_fixed_window(monkeypatch) -> None:
    """The exact scenario a plain fixed-window counter gets wrong: 5 hits in the last
    instant of window 0, then 5 more in the very first instant of window 1. A plain
    fixed-window counter would allow all 10 (each window's own raw count stays <= 5).
    The sliding-window counter blends window 0's count into window 1's early estimate
    and must reject some of the second burst."""
    limiter = _limiter()
    current_time = {"t": 59.999}
    monkeypatch.setattr("app.utils.rate_limit.time.time", lambda: current_time["t"])

    first_burst = [await limiter.hit("k", 5, window_seconds=60) for _ in range(5)]
    assert first_burst == [True] * 5

    current_time["t"] = 60.001  # next window, effectively no real time elapsed
    second_burst = [await limiter.hit("k", 5, window_seconds=60) for _ in range(5)]
    assert False in second_burst, "sliding window must reject part of the second burst"


async def test_previous_window_weight_decays_as_current_window_progresses(
    monkeypatch,
) -> None:
    """Two independent limiters, each seeded with 5 hits in window 0 (t=0) so behavior
    can be compared at two different points inside window 1 without one probe's hits
    contaminating the other's count."""
    limiter_early = _limiter()
    time_early = {"t": 0.0}
    monkeypatch.setattr("app.utils.rate_limit.time.time", lambda: time_early["t"])
    for _ in range(5):
        assert await limiter_early.hit("k", 5, window_seconds=60) is True
    # Just after entering window 1, window 0's weight has barely decayed — almost
    # none of a fresh 5-burst should be allowed.
    time_early["t"] = 60.001
    allowed_early = sum([await limiter_early.hit("k", 5, window_seconds=60) for _ in range(5)])

    limiter_late = _limiter()
    time_late = {"t": 0.0}
    monkeypatch.setattr("app.utils.rate_limit.time.time", lambda: time_late["t"])
    for _ in range(5):
        assert await limiter_late.hit("k", 5, window_seconds=60) is True
    # Almost an entire window later, window 0's weight has decayed close to zero —
    # most of a fresh 5-burst should be allowed again.
    time_late["t"] = 119.9
    allowed_late = sum([await limiter_late.hit("k", 5, window_seconds=60) for _ in range(5)])

    assert allowed_early == 0
    assert allowed_late > allowed_early
    assert allowed_late >= 4


async def test_independent_keys_do_not_share_a_limit(monkeypatch) -> None:
    limiter = _limiter()
    monkeypatch.setattr("app.utils.rate_limit.time.time", lambda: 1000.0)
    for _ in range(3):
        assert await limiter.hit("a", 3, window_seconds=60) is True
    assert await limiter.hit("a", 3, window_seconds=60) is False
    assert await limiter.hit("b", 3, window_seconds=60) is True
