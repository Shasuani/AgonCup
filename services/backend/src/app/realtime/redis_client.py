"""Process-wide async Redis client for the realtime plane (one pool, reused).

Both the WS hub (command pub/sub) and the orchestration command publisher need a
``redis.asyncio`` client. Creating one per call (the earlier ``_redis_client()``)
opens a fresh connection pool each time; this module hands out a single lazily-
constructed client so the gateway and the publisher share one pool (PLAN.md §5).

Import-safe: ``from_url`` builds a lazy pool (no connection until first command),
and the ``redis`` import is deferred, so importing this module performs no IO.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings

_client: Any = None


def get_redis() -> Any:
    """Return the process-wide async Redis client (lazy singleton)."""
    global _client
    if _client is None:
        from redis.asyncio import Redis  # noqa: PLC0415 - deferred so import stays IO-free

        _client = Redis.from_url(get_settings().redis_url)
    return _client


async def aclose_redis() -> None:
    """Close the shared client (call on app shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
