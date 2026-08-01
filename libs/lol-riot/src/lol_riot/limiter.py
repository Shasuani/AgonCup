"""A tiny, real async token-bucket rate limiter.

Protects the one-time Account-V1 linking + verification polling against a
personal/standard key budget (e.g. ~20 req/s). This is intentionally small and
fully implemented (and tested) — it is *not* the per-region tournament-scale
limiter the removed v2 Tournament API would have needed (PLAN.md §3.5).
"""

from __future__ import annotations

import asyncio
from time import monotonic
from types import TracebackType


class TokenBucketLimiter:
    """Async token-bucket limiter.

    Tokens refill continuously at ``rate`` tokens/second up to ``capacity``.
    ``acquire`` blocks until a token is available. Safe under concurrency: a
    single lock serializes the refill/spend critical section.
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        time_fn: object = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._rate = float(rate)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        # Injectable clock for deterministic tests; defaults to monotonic().
        self._now = time_fn if callable(time_fn) else monotonic
        self._updated_at = self._clock()
        self._lock = asyncio.Lock()

    def _clock(self) -> float:
        return float(self._now())

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def capacity(self) -> float:
        return self._capacity

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated_at
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated_at = now

    @property
    def available(self) -> float:
        """Best-effort current token count (refilled, not spent)."""
        self._refill()
        return self._tokens

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until ``tokens`` can be spent, then spend them."""
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        if tokens > self._capacity:
            raise ValueError("tokens exceeds bucket capacity")
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(wait)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking spend; returns True iff tokens were available."""
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def __aenter__(self) -> TokenBucketLimiter:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


__all__ = ["TokenBucketLimiter"]
