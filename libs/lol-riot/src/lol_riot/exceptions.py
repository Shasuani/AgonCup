"""Typed errors for the Riot Account-V1 client.

A small, dependency-free exception hierarchy so callers (backend / worker, see
PLAN.md §3.1) can branch on the failure mode of a Riot Web API call without
parsing ``httpx`` status codes themselves. Account-V1 is the only Riot Web API
used in v3 (PLAN.md §3.5), so this hierarchy is deliberately tiny.
"""

from __future__ import annotations


class RiotApiError(Exception):
    """Base class for all Riot Web API failures.

    Attributes
    ----------
    status_code:
        The HTTP status code returned by Riot, when one was received
        (``None`` for transport-level failures).
    """

    status_code: int | None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotFound(RiotApiError):
    """Raised on a ``404`` — e.g. an unknown Riot ID / PUUID.

    A 404 from ``by-riot-id`` is an expected, non-exceptional outcome of a
    user-supplied Riot ID and callers are expected to handle it (PLAN.md §3.5).
    """

    def __init__(self, message: str = "Riot resource not found") -> None:
        super().__init__(message, status_code=404)


class RateLimited(RiotApiError):
    """Raised on a ``429`` once the bounded retry budget is exhausted.

    Carries the last-seen ``Retry-After`` (seconds) so the caller / worker can
    schedule a later attempt (PLAN.md §3.5: honor 429 ``Retry-After``).
    """

    def __init__(
        self,
        message: str = "Riot API rate limited",
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


__all__ = ["NotFound", "RateLimited", "RiotApiError"]
