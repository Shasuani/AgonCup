"""Notification subscriber tests (PLAN.md §3.2 internal push fan-out).

Offline by design: Redis + its pubsub are FAKED in-memory and the backend client
is a stub. We assert that an envelope is parsed, the originating guild is resolved
to its configured notify channel, and the matching ``lol_discord`` embed is posted
to that channel — and that bad input never raises (resilience contract).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import discord

from app.notifications import NotificationSubscriber, _build_embed, _parse_envelope


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeChannel(discord.abc.Messageable):
    """Minimal Messageable so _resolve_channel's isinstance check passes."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[discord.Embed] = []

    async def _get_channel(self) -> Any:  # pragma: no cover - Messageable contract
        return self

    async def send(self, *, embed: discord.Embed, allowed_mentions: Any = None) -> None:  # type: ignore[override]
        self.sent.append(embed)


class _FakeGuild:
    def __init__(self, guild_id: int, *, name: str, channel: _FakeChannel) -> None:
        self.id = guild_id
        self.name = name
        self._channel = channel

    def get_channel(self, channel_id: int) -> Any:
        return self._channel if channel_id == self._channel.id else None


class _FakeUser:
    id = 42


class _FakeBot:
    def __init__(self, guilds: list[_FakeGuild]) -> None:
        self.guilds = guilds
        self.user = _FakeUser()

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        return next((g for g in self.guilds if g.id == guild_id), None)


class _FakeBackend:
    """Stub bridge client: maps Discord guild id -> backend resolve/onboard response."""

    def __init__(self, mapping: dict[int, dict[str, Any]]) -> None:
        self._mapping = mapping
        self.calls: list[int] = []

    async def onboard_guild(self, ctx: Any, *, name: str) -> dict[str, Any]:
        self.calls.append(ctx.discord_guild_id)
        return self._mapping[ctx.discord_guild_id]

    async def resolve_guild(self, ctx: Any) -> dict[str, Any]:
        # Read-only routing lookup (must NOT assert admin); raises on a foreign
        # guild just like the real /guilds/resolve (GuildNotOnboarded -> error).
        self.calls.append(ctx.discord_guild_id)
        return self._mapping[ctx.discord_guild_id]


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def listen(self) -> Any:
        for msg in self._messages:
            yield msg


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        return None


def _envelope(origin_guild_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "message",
        "data": json.dumps(
            {"schema_version": "1", "origin_guild_id": origin_guild_id, "payload": payload}
        ),
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def test_envelope_routed_to_configured_channel() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    bot = _FakeBot([guild])
    backend = _FakeBackend(
        {
            222: {
                "guild_id": "g-backend",
                "discord_guild_id": 222,
                "settings": {"notify_channel_id": 555},
            }
        }
    )
    pubsub = _FakePubSub(
        [
            _envelope(
                "g-backend",
                {"kind": "registration_approved", "tournament_name": "Cup", "team_name": "Aces"},
            )
        ]
    )
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())

    assert pubsub.subscribed == ["lol:notifications"]
    assert len(channel.sent) == 1
    assert isinstance(channel.sent[0], discord.Embed)


def test_foreign_guild_is_rejected() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    bot = _FakeBot([guild])
    backend = _FakeBackend(
        {
            222: {
                "guild_id": "g-backend",
                "discord_guild_id": 222,
                "settings": {"notify_channel_id": 555},
            }
        }
    )
    # Envelope for a guild the bot does NOT serve.
    pubsub = _FakePubSub(
        [
            _envelope(
                "g-other",
                {"kind": "registration_approved", "tournament_name": "X", "team_name": "Y"},
            )
        ]
    )
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())
    assert channel.sent == []


def test_malformed_payload_does_not_crash() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    bot = _FakeBot([guild])
    backend = _FakeBackend({222: {"guild_id": "g", "settings": {"notify_channel_id": 555}}})
    pubsub = _FakePubSub(
        [
            {"type": "message", "data": "not-json"},
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": json.dumps({"origin_guild_id": "g", "payload": "bad"})},
        ]
    )
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    # Must complete without raising; nothing posted.
    asyncio.run(sub.run())
    assert channel.sent == []


def test_unknown_kind_is_ignored() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    bot = _FakeBot([guild])
    backend = _FakeBackend(
        {222: {"guild_id": "g", "discord_guild_id": 222, "settings": {"notify_channel_id": 555}}}
    )
    pubsub = _FakePubSub([_envelope("g", {"kind": "brand_new_kind", "foo": "bar"})])
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())
    assert channel.sent == []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_parse_envelope_handles_bytes_and_dict() -> None:
    assert _parse_envelope(b'{"a": 1}') == {"a": 1}
    assert _parse_envelope('{"a": 1}') == {"a": 1}
    assert _parse_envelope({"a": 1}) == {"a": 1}
    assert _parse_envelope("nope") is None
    assert _parse_envelope(123) is None


def test_build_embed_covers_every_kind() -> None:
    assert (
        _build_embed("registration_approved", {"tournament_name": "C", "team_name": "T"})
        is not None
    )
    assert (
        _build_embed("check_in_reminder", {"tournament_name": "C", "deadline": "5pm"}) is not None
    )
    assert _build_embed("match_ready", {"match_label": "R1", "host_name": "H"}) is not None
    assert (
        _build_embed(
            "result_recorded",
            {"match_label": "R1", "winner_name": "A", "loser_name": "B", "decided_by": "reported"},
        )
        is not None
    )
    assert _build_embed("unknown", {}) is None
