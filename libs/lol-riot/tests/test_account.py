"""Unit tests for the real Account-V1 client (no live network).

All HTTP is served by an ``httpx.MockTransport`` injected into the client, so
these exercise routing, URL-encoding, the ``X-Riot-Token`` header, the bounded
429 retry, and the typed-error mapping (PLAN.md §3.5) without touching Riot.
"""

from __future__ import annotations

import httpx
import pytest

from lol_riot import (
    AccountDTO,
    ContinentalRoute,
    NotFound,
    PlatformRoute,
    RateLimited,
    RiotAccountClient,
    RiotApiError,
    SummonerDTO,
    TokenBucketLimiter,
)


def _fast_limiter() -> TokenBucketLimiter:
    """A limiter that never blocks the test (huge rate + capacity)."""
    return TokenBucketLimiter(rate=1_000_000.0, capacity=1_000_000.0)


def _client(
    handler: object, *, route: ContinentalRoute = ContinentalRoute.AMERICAS, **kw: object
) -> RiotAccountClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport)
    return RiotAccountClient(
        "secret-key",
        route,
        client=http,
        limiter=_fast_limiter(),
        **kw,  # type: ignore[arg-type]
    )


async def test_get_account_by_riot_id_success() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("X-Riot-Token")
        return httpx.Response(
            200,
            json={"puuid": "PUUID-123", "gameName": "Faker", "tagLine": "KR1"},
        )

    client = _client(handler, route=ContinentalRoute.ASIA)
    dto = await client.get_account_by_riot_id("Faker", "KR1")

    assert isinstance(dto, AccountDTO)
    assert dto.puuid == "PUUID-123"
    assert dto.game_name == "Faker"
    assert dto.tag_line == "KR1"
    # Continental host + canonical path + token header.
    assert seen["url"] == (
        "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
    )
    assert seen["token"] == "secret-key"
    await client.aclose()


async def test_get_account_by_riot_id_url_encodes_segments() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw_path"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"puuid": "p", "gameName": "x", "tagLine": "y"})

    client = _client(handler)
    # Names with spaces / reserved chars must be percent-encoded per-segment.
    await client.get_account_by_riot_id("Hide on bush", "EU/W")

    assert "Hide%20on%20bush" in seen["raw_path"]
    # '/' inside the tag must be encoded, not treated as a path separator.
    assert "EU%2FW" in seen["raw_path"]
    await client.aclose()


async def test_get_account_by_riot_id_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": {"status_code": 404}})

    client = _client(handler)
    with pytest.raises(NotFound) as exc:
        await client.get_account_by_riot_id("Nobody", "ZZZ")
    assert exc.value.status_code == 404
    await client.aclose()


async def test_get_account_by_riot_id_429_then_success_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("lol_riot.account.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"puuid": "OK", "gameName": "g", "tagLine": "t"})

    client = _client(handler, max_retries=2)
    dto = await client.get_account_by_riot_id("g", "t")
    assert dto.puuid == "OK"
    assert calls["n"] == 2  # one 429, one success
    # Honored the Retry-After cooldown once before the retry.
    assert slept == [2.0]
    await client.aclose()


async def test_get_account_by_riot_id_429_exhausts_retries_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("lol_riot.account.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "3"})

    client = _client(handler, max_retries=2)
    with pytest.raises(RateLimited) as exc:
        await client.get_account_by_riot_id("g", "t")
    assert exc.value.status_code == 429
    assert exc.value.retry_after == pytest.approx(3.0)
    # 1 initial + 2 retries = 3 attempts.
    assert calls["n"] == 3
    await client.aclose()


async def test_unexpected_status_raises_riot_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": {"status_code": 500}})

    client = _client(handler)
    with pytest.raises(RiotApiError) as exc:
        await client.get_account_by_riot_id("g", "t")
    assert exc.value.status_code == 500
    assert not isinstance(exc.value, (NotFound, RateLimited))
    await client.aclose()


async def test_transport_error_raises_riot_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    client = _client(handler)
    with pytest.raises(RiotApiError):
        await client.get_account_by_riot_id("g", "t")
    await client.aclose()


async def test_summoner_by_puuid_uses_platform_host() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"puuid": "P", "profileIconId": 4567, "id": "SUMM-1"})

    # Account-V1 route is continental, but Summoner-V4 must hit the platform host.
    client = _client(handler, route=ContinentalRoute.EUROPE)
    dto = await client.get_summoner_by_puuid("P", PlatformRoute.EUW1)

    assert isinstance(dto, SummonerDTO)
    assert dto.profile_icon_id == 4567
    assert dto.summoner_id == "SUMM-1"
    assert seen["url"] == ("https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/P")
    await client.aclose()


async def test_summoner_by_puuid_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(NotFound):
        await client.get_summoner_by_puuid("missing", PlatformRoute.NA1)
    await client.aclose()


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        RiotAccountClient("k", max_retries=-1)


async def test_async_context_manager_closes_owned_client() -> None:
    async with RiotAccountClient("k") as client:
        assert client.route is ContinentalRoute.AMERICAS
    # No assertion needed beyond clean enter/exit; owned client is closed.


def test_platform_route_base_url() -> None:
    assert PlatformRoute.NA1.base_url == "https://na1.api.riotgames.com"
    assert PlatformRoute.KR.base_url == "https://kr.api.riotgames.com"
