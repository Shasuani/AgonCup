"""Riot Account-V1 client (the only Riot Web API used in v3).

Account-V1 resolves a Riot ID (``gameName`` + ``tagLine``) to a canonical,
immutable PUUID via
``GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`` on a
**continental** cluster (PLAN.md §3.5). PUUID is the canonical identity key
(PLAN.md §2); ``display_name`` is stale, refreshable, NON-identity data.

This module also exposes a *minimal* Summoner-V4 ``by-puuid`` call used only to
read ``profileIconId`` for the ownership-challenge nonce (PLAN.md §3.1
verification). Summoner-V4 is **platform-routed** (``na1``/``euw1``/...), not
continental — see :class:`lol_riot.routing.PlatformRoute`. It also exposes a
*minimal* League-V4 ``entries/by-puuid`` call (also platform-routed) used to
read the Solo/Duo rank snapshot at desktop verify time (best-effort, PLAN.md
§3.5). Everything else (Tournament-V5, provider/callback, Match-V5) is out of
scope for v3.

All calls go through the shared :class:`~lol_riot.limiter.TokenBucketLimiter`
(personal/standard key budget, PLAN.md §3.5), honor a ``429`` ``Retry-After``
with a bounded retry, and raise typed errors from :mod:`lol_riot.exceptions`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from lol_riot.exceptions import NotFound, RateLimited, RiotApiError
from lol_riot.limiter import TokenBucketLimiter
from lol_riot.routing import ContinentalRoute, PlatformRoute

_logger = logging.getLogger("lol_riot.account")

# Personal/standard key budget (PLAN.md §3.5: ~20 req/s, 100 req/2min).
_DEFAULT_RATE = 20.0
_DEFAULT_CAPACITY = 20.0

# Bounded 429 retry (PLAN.md §3.5: honor Retry-After, but never loop forever).
_DEFAULT_MAX_RETRIES = 3
# Fallback wait when Riot omits / malforms the Retry-After header.
_FALLBACK_RETRY_AFTER = 1.0
# Defensive ceiling so a hostile/huge Retry-After can't pin a worker (seconds).
_MAX_RETRY_AFTER = 120.0

_DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class AccountDTO:
    """Typed view of the Account-V1 ``by-riot-id`` response.

    PUUID is the canonical, immutable identity key (PLAN.md §2). ``game_name``
    and ``tag_line`` are stale, refreshable display data — **never** matched on
    for identity.
    """

    puuid: str
    game_name: str
    tag_line: str


@dataclass(frozen=True, slots=True)
class LeagueEntryDTO:
    """Typed view of a League-V4 ranked entry (PLAN.md §3.5 rank snapshot).

    One element of the ``GET /lol/league/v4/entries/by-puuid/{puuid}`` array
    (League-V4 is **platform-routed**, like Summoner-V4). The caller selects the
    ``RANKED_SOLO_5x5`` entry for the Solo/Duo rank. ``rank`` is the roman
    division (``"II"``) and is **empty** for apex tiers (MASTER and above).
    """

    queue_type: str
    tier: str
    rank: str
    league_points: int
    wins: int
    losses: int


@dataclass(frozen=True, slots=True)
class SummonerDTO:
    """Minimal Summoner-V4 ``by-puuid`` view (profile-icon nonce only).

    Only the fields the verification icon check needs (PLAN.md §3.1):
    ``puuid`` (echo) and ``profile_icon_id`` (the value the nonce challenge
    compares against). ``summoner_id`` is persisted alongside the PUUID
    (PLAN.md §2 ``RiotAccount.summoner_id``).
    """

    puuid: str
    profile_icon_id: int
    summoner_id: str | None = None


class RiotAccountClient:
    """Async Account-V1 client (Account-V1-first; minimal Summoner-V4 nonce).

    Parameters
    ----------
    api_key:
        Riot personal/standard API key (Account-V1 only).
    route:
        Continental routing cluster (``americas | asia | europe``) for
        Account-V1 (PLAN.md §3.5).
    limiter:
        Optional shared token-bucket limiter; one is created if omitted.
    client:
        Optional injected ``httpx.AsyncClient`` (for tests / connection reuse).
        When omitted, one is created lazily and owned (closed by
        :meth:`aclose`).
    max_retries:
        Bounded number of ``429`` retries before :class:`RateLimited` is raised.
    """

    def __init__(
        self,
        api_key: str,
        route: ContinentalRoute = ContinentalRoute.AMERICAS,
        *,
        limiter: TokenBucketLimiter | None = None,
        client: httpx.AsyncClient | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._api_key = api_key
        self._route = route
        self._limiter = limiter or TokenBucketLimiter(
            rate=_DEFAULT_RATE, capacity=_DEFAULT_CAPACITY
        )
        self._client = client
        self._owns_client = client is None
        self._max_retries = max_retries

    @property
    def route(self) -> ContinentalRoute:
        return self._route

    def _headers(self) -> dict[str, str]:
        return {"X-Riot-Token": self._api_key}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        return self._client

    @staticmethod
    def _by_riot_id_path(game_name: str, tag_line: str) -> str:
        # URL-encode each path segment; ``safe=""`` so '/' inside a name (and
        # other reserved chars) cannot inject extra path segments.
        gn = quote(game_name, safe="")
        tl = quote(tag_line, safe="")
        return f"/riot/account/v1/accounts/by-riot-id/{gn}/{tl}"

    @staticmethod
    def _summoner_by_puuid_path(puuid: str) -> str:
        return f"/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}"

    @staticmethod
    def _league_entries_by_puuid_path(puuid: str) -> str:
        return f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}"

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        """Parse the ``Retry-After`` header (seconds), bounded and defended.

        Riot sends integer-seconds; we clamp to a sane ceiling and fall back to
        a small constant on a missing / malformed header (PLAN.md §3.5).
        """
        raw = response.headers.get("Retry-After")
        if raw is None:
            return _FALLBACK_RETRY_AFTER
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return _FALLBACK_RETRY_AFTER
        if seconds <= 0:
            return _FALLBACK_RETRY_AFTER
        return min(seconds, _MAX_RETRY_AFTER)

    async def _request(self, base_url: str, path: str, *, what: str) -> httpx.Response:
        """Perform a rate-limited GET with bounded 429 retry + typed errors.

        Returns the ``2xx`` response. Raises :class:`NotFound` on ``404``,
        :class:`RateLimited` once the retry budget is exhausted, and
        :class:`RiotApiError` for any other non-2xx / transport failure
        (PLAN.md §3.5).
        """
        url = f"{base_url}{path}"
        headers = self._headers()
        retry_after: float | None = None

        # 1 initial attempt + up to ``max_retries`` retries on 429.
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                response = await self._http().get(url, headers=headers)
            except httpx.HTTPError as exc:  # transport-level (timeout, DNS, ...)
                raise RiotApiError(f"{what}: transport error: {exc}") from exc

            status = response.status_code
            if status == 404:
                raise NotFound(f"{what}: not found")
            if status == 429:
                retry_after = self._parse_retry_after(response)
                _logger.warning(
                    "riot 429 rate limited",
                    extra={
                        "what": what,
                        "attempt": attempt,
                        "retry_after": retry_after,
                    },
                )
                if attempt < self._max_retries:
                    # Honor the server-dictated cooldown directly: the limiter
                    # governs steady-state throughput, but a 429 Retry-After is
                    # an explicit, orthogonal backoff Riot tells us to wait.
                    if retry_after > 0:
                        await asyncio.sleep(retry_after)
                    continue
                raise RateLimited(
                    f"{what}: rate limited after {attempt + 1} attempts",
                    retry_after=retry_after,
                )
            if status >= 400:
                raise RiotApiError(f"{what}: unexpected status {status}", status_code=status)
            return response

        # Unreachable: the loop either returns a 2xx or raises. Guard for typing.
        raise RateLimited(f"{what}: rate limited", retry_after=retry_after)

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        """Resolve a Riot ID to an :class:`AccountDTO` (PLAN.md §3.5).

        ``GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`` on the
        continental cluster. Path segments are URL-encoded. A ``404`` (unknown
        Riot ID) raises :class:`NotFound`; ``429`` is retried up to the bounded
        budget then raises :class:`RateLimited`.
        """
        path = self._by_riot_id_path(game_name, tag_line)
        response = await self._request(self._route.base_url, path, what="account_by_riot_id")
        data = response.json()
        return AccountDTO(
            puuid=data["puuid"],
            game_name=data.get("gameName", game_name),
            tag_line=data.get("tagLine", tag_line),
        )

    async def get_summoner_by_puuid(self, puuid: str, platform: PlatformRoute) -> SummonerDTO:
        """Read ``profileIconId`` for the ownership-challenge nonce.

        Minimal Summoner-V4 ``GET /lol/summoner/v4/summoners/by-puuid/{puuid}``.
        **Platform-routed** (PLAN.md §3.5): Account-V1 is continental, but
        Summoner-V4 is hosted per-platform (``na1``/``euw1``/...), so the
        caller must pass the player's :class:`PlatformRoute`, not a continent.
        Used only by the verification icon check (PLAN.md §3.1).
        """
        path = self._summoner_by_puuid_path(puuid)
        response = await self._request(platform.base_url, path, what="summoner_by_puuid")
        data = response.json()
        return SummonerDTO(
            puuid=data.get("puuid", puuid),
            profile_icon_id=int(data["profileIconId"]),
            summoner_id=data.get("id"),
        )

    async def get_league_entries_by_puuid(
        self, puuid: str, platform: PlatformRoute
    ) -> list[LeagueEntryDTO]:
        """Read ranked entries for a PUUID (PLAN.md §3.5 rank snapshot).

        League-V4 ``GET /lol/league/v4/entries/by-puuid/{puuid}``.
        **Platform-routed** (``na1``/``euw1``/...), like Summoner-V4 — so the
        caller passes the player's :class:`PlatformRoute`, not a continent. A
        ``200`` with an empty array means *fully unranked* (returns ``[]``).

        This raises typed errors (``NotFound``/``RateLimited``/``RiotApiError``)
        like every other call so the limiter/retry semantics stay uniform; the
        **best-effort** policy (treat any failure as "rank unknown") lives in
        the backend caller, NOT here.
        """
        path = self._league_entries_by_puuid_path(puuid)
        response = await self._request(
            platform.base_url, path, what="league_entries_by_puuid"
        )
        data = response.json()
        return [
            LeagueEntryDTO(
                queue_type=e.get("queueType", ""),
                tier=e.get("tier", ""),
                rank=e.get("rank", ""),
                league_points=int(e.get("leaguePoints", 0)),
                wins=int(e.get("wins", 0)),
                losses=int(e.get("losses", 0)),
            )
            for e in data
        ]

    async def aclose(self) -> None:
        """Close the HTTP client if this instance owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> RiotAccountClient:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        await self.aclose()


__all__ = ["AccountDTO", "LeagueEntryDTO", "RiotAccountClient", "SummonerDTO"]
