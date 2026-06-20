"""Backend bridge client tests (PLAN.md §3.2 thin presentation layer).

Offline by design: ``httpx.AsyncClient`` is fully MOCKED. We assert that every
method builds the correct service-auth request — the ``X-Service-Token`` header
and the JSON body (``ctx`` + payload) — and that non-2xx responses raise a
``BackendError`` carrying the backend ``detail``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import app.backend_client as bc
from app.backend_client import BackendClient, BackendError, DiscordContext

CTX = DiscordContext(
    discord_user_id=111,
    discord_guild_id=222,
    is_discord_guild_admin=True,
    display_name="Tester",
)


class _MockResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class _MockAsyncClient:
    """Records the single POST so tests can assert URL/headers/body."""

    last_post: dict[str, Any] = {}
    next_response: _MockResponse = _MockResponse(200, {})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        type(self).last_post = {"url": url, **kwargs}
        return type(self).next_response


def _client(monkeypatch: Any, response: _MockResponse) -> BackendClient:
    _MockAsyncClient.next_response = response
    monkeypatch.setattr(bc.httpx, "AsyncClient", _MockAsyncClient)
    return BackendClient(base_url="http://backend:8000", service_token="shh")


def test_onboard_guild_sends_service_auth(monkeypatch: Any) -> None:
    client = _client(
        monkeypatch,
        _MockResponse(
            200,
            {
                "guild_id": "g1",
                "discord_guild_id": 222,
                "created": True,
                "admin_seeded": True,
                "settings": {"notify_channel_id": 5},
            },
        ),
    )
    out = asyncio.run(client.onboard_guild(CTX, name="My Guild"))

    sent = _MockAsyncClient.last_post
    assert sent["url"] == "http://backend:8000/api/v1/integrations/discord/guilds/onboard"
    assert sent["headers"]["X-Service-Token"] == "shh"
    assert sent["json"]["name"] == "My Guild"
    assert sent["json"]["ctx"] == {
        "discord_user_id": 111,
        "discord_guild_id": 222,
        "is_discord_guild_admin": True,
        "display_name": "Tester",
    }
    assert out["guild_id"] == "g1"


def test_link_account_uses_riot_aliases(monkeypatch: Any) -> None:
    client = _client(
        monkeypatch,
        _MockResponse(
            200, {"verified": False, "target_icon_id": 7, "game_name": "X", "tag_line": "EU"}
        ),
    )
    asyncio.run(client.link_account(CTX, game_name="X", tag_line="EU", region="euw1"))

    sent = _MockAsyncClient.last_post
    assert sent["url"].endswith("/integrations/discord/accounts/link")
    assert sent["json"]["gameName"] == "X"
    assert sent["json"]["tagLine"] == "EU"
    assert sent["json"]["region"] == "euw1"


def test_create_tournament_payload(monkeypatch: Any) -> None:
    client = _client(
        monkeypatch, _MockResponse(200, {"id": "t1", "name": "Cup", "format": "single_elim"})
    )
    asyncio.run(client.create_tournament(CTX, name="Cup", max_teams=8))

    sent = _MockAsyncClient.last_post
    assert sent["url"].endswith("/tournaments/create")
    assert sent["json"]["name"] == "Cup"
    assert sent["json"]["max_teams"] == 8


def test_leave_team_returns_none_on_204(monkeypatch: Any) -> None:
    client = _client(monkeypatch, _MockResponse(204, None))
    result = asyncio.run(client.leave_team(CTX, team_id="abc"))
    assert result is None
    assert _MockAsyncClient.last_post["json"]["team_id"] == "abc"


def test_list_tournaments_returns_list(monkeypatch: Any) -> None:
    client = _client(monkeypatch, _MockResponse(200, [{"id": "t1"}, {"id": "t2"}]))
    rows = asyncio.run(client.list_tournaments(CTX))
    assert [r["id"] for r in rows] == ["t1", "t2"]


def test_error_raises_backend_error_with_detail(monkeypatch: Any) -> None:
    client = _client(monkeypatch, _MockResponse(409, {"detail": "guild is not onboarded"}))
    try:
        asyncio.run(client.create_team(CTX, name="Z"))
    except BackendError as exc:
        assert exc.status_code == 409
        assert exc.detail == "guild is not onboarded"
    else:  # pragma: no cover
        raise AssertionError("expected BackendError")
