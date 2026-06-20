"""Thin Riot Account-V1 client skeleton.

Account-V1 is the *only* Riot Web API used in v3 (PLAN.md §3.5): resolve a
Riot ID (``gameName`` + ``tagLine``) to a canonical, immutable PUUID via
``GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`` on a
continental cluster. No Tournament-V5, no Match-V5.

Phase-0: the network call is a stub (raises NotImplementedError). The return
type is a small local typed DTO so this lib is standalone; once ``lol-schemas``
lands it can be swapped for the canonical DTO (see pyproject ``[schemas]`` extra).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from lol_riot.limiter import TokenBucketLimiter
from lol_riot.routing import ContinentalRoute

# Personal/standard key budget (PLAN.md §3.5: ~20 req/s, 100 req/2min).
_DEFAULT_RATE = 20.0
_DEFAULT_CAPACITY = 20.0


@dataclass(frozen=True, slots=True)
class AccountDTO:
    """Typed stub for the Account-V1 ``by-riot-id`` response.

    PUUID is the canonical, immutable identity key (PLAN.md §2).
    """

    puuid: str
    game_name: str
    tag_line: str


class RiotAccountClient:
    """Async Account-V1 client.

    Parameters
    ----------
    api_key:
        Riot personal/standard API key (Account-V1 only).
    route:
        Continental routing cluster (``americas | asia | europe``).
    limiter:
        Optional shared token-bucket limiter; one is created if omitted.
    client:
        Optional injected ``httpx.AsyncClient`` (for tests / connection reuse).
    """

    def __init__(
        self,
        api_key: str,
        route: ContinentalRoute = ContinentalRoute.AMERICAS,
        *,
        limiter: TokenBucketLimiter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._route = route
        self._limiter = limiter or TokenBucketLimiter(
            rate=_DEFAULT_RATE, capacity=_DEFAULT_CAPACITY
        )
        self._client = client

    @property
    def route(self) -> ContinentalRoute:
        return self._route

    def _headers(self) -> dict[str, str]:
        return {"X-Riot-Token": self._api_key}

    def _by_riot_id_path(self, game_name: str, tag_line: str) -> str:
        return f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"

    async def get_account_by_riot_id(
        self, game_name: str, tag_line: str
    ) -> AccountDTO:
        """Resolve a Riot ID to an :class:`AccountDTO`.

        Phase-0 stub: rate-limit budget is honored, but the HTTP call is not yet
        wired. Implementation lands in Phase 1 (account linking).
        """
        await self._limiter.acquire()
        _ = (self._headers(), self._by_riot_id_path(game_name, tag_line))
        raise NotImplementedError(
            "Account-V1 by-riot-id call is a Phase-0 stub; wired in Phase 1."
        )

    async def aclose(self) -> None:
        """Close the owned HTTP client, if any."""
        if self._client is not None:
            await self._client.aclose()


__all__ = ["AccountDTO", "RiotAccountClient"]
