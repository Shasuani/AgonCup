"""Process-local fixed-window rate limiter shared across routers (PLAN.md §3.1).

A small, dependency-free brake against credential-guessing / brute-force on the
unauthenticated (and a few authenticated) endpoints — the account verify/desktop
flows and ``/auth/desktop-pair`` + ``/auth/refresh``. Each limiter instance owns
its OWN bucket dict so distinct concerns (per-IP verify vs per-IP refresh) never
share a window.

TODO(scale): this is PROCESS-LOCAL; it gives ZERO cross-replica protection. Move
to a shared Redis counter when the backend runs >1 replica. Adequate as a
single-node guess-rate brake.

PROXY STORY: callers key on the socket peer (``request.client.host``). This is
correct only single-hop (direct client connections). Behind a reverse proxy every
request shares the proxy IP, collapsing all clients into one bucket
(over-throttling). We deliberately DO NOT trust ``X-Forwarded-For`` — it is
client-spoofable and would let an attacker bypass the limiter by rotating a forged
header. If a trusted proxy is deployed, switch to a proxy-set header validated
against a known proxy allowlist before keying on it.
"""

from __future__ import annotations

import time
from collections import deque


class FixedWindowLimiter:
    """A per-key fixed-window limiter: at most ``max_hits`` per ``window_s``.

    Fully-decayed keys are pruned on every call so a key-rotating attacker cannot
    grow the bucket dict without bound (slow memory leak): each call sweeps stale
    keys opportunistically and the requesting key is dropped if it ends up empty.
    """

    def __init__(self, *, max_hits: int, window_s: float) -> None:
        self._max = max_hits
        self._window = window_s
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit for ``key``; return ``False`` when its window is exhausted."""
        now = now if now is not None else time.monotonic()
        # Opportunistic sweep of stale keys (bounds the dict under key rotation).
        stale = [k for k, h in self._hits.items() if not h or now - h[-1] > self._window]
        for k in stale:
            del self._hits[k]
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True


__all__ = ["FixedWindowLimiter"]
