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


class _FakeDMUser:
    """A Discord user we can DM; records embeds or raises a configured error."""

    def __init__(self, user_id: int, *, raise_on_send: Exception | None = None) -> None:
        self.id = user_id
        self._raise_on_send = raise_on_send
        self.sent: list[discord.Embed] = []

    async def send(self, *, embed: discord.Embed, allowed_mentions: Any = None) -> None:
        if self._raise_on_send is not None:
            raise self._raise_on_send
        self.sent.append(embed)


class _FakeBot:
    def __init__(
        self,
        guilds: list[_FakeGuild],
        *,
        users: dict[int, _FakeDMUser] | None = None,
        fetch_raises: Exception | None = None,
    ) -> None:
        self.guilds = guilds
        self.user = _FakeUser()
        self._users = users or {}
        self._fetch_raises = fetch_raises
        self.fetch_calls: list[int] = []

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_user(self, user_id: int) -> _FakeDMUser | None:
        return self._users.get(user_id)

    async def fetch_user(self, user_id: int) -> _FakeDMUser:
        self.fetch_calls.append(user_id)
        if self._fetch_raises is not None:
            raise self._fetch_raises
        if user_id in self._users:
            return self._users[user_id]
        raise discord.NotFound(_FakeResponse(), "unknown user")  # type: ignore[arg-type]


class _FakeResponse:
    """Minimal aiohttp-style response so discord.NotFound can be constructed."""

    status = 404
    reason = "Not Found"


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
# registration_verified — DM routing with guild-channel fallback (PLAN.md §3.2)
# ---------------------------------------------------------------------------
def _verified_envelope(origin_guild_id: str, discord_user_id: str) -> dict[str, Any]:
    return _envelope(
        origin_guild_id,
        {
            "kind": "registration_verified",
            "discord_user_id": discord_user_id,
            "riot_id": "Chama#Shasu",
            "region": "EUNE",
            "rank_text": "Gold II, 45 LP",
        },
    )


def test_registration_verified_is_dmed_to_user() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    dm_user = _FakeDMUser(9001)
    bot = _FakeBot([guild], users={9001: dm_user})
    backend = _FakeBackend(
        {
            222: {
                "guild_id": "g-backend",
                "discord_guild_id": 222,
                "settings": {"notify_channel_id": 555},
            }
        }
    )
    pubsub = _FakePubSub([_verified_envelope("g-backend", "9001")])
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())

    # DMed the user; did NOT post to the guild channel.
    assert len(dm_user.sent) == 1
    assert isinstance(dm_user.sent[0], discord.Embed)
    assert channel.sent == []


def test_registration_verified_dropped_when_dm_forbidden() -> None:
    # PRIVACY: registration_verified is DM-ONLY. When the DM fails (DMs closed)
    # we DROP it (log only) — we never broadcast the user's Riot ID/region/rank
    # to a public guild channel without consent.
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    dm_user = _FakeDMUser(9001, raise_on_send=discord.Forbidden(_FakeResponse(), "DMs closed"))  # type: ignore[arg-type]
    bot = _FakeBot([guild], users={9001: dm_user})
    backend = _FakeBackend(
        {
            222: {
                "guild_id": "g-backend",
                "discord_guild_id": 222,
                "settings": {"notify_channel_id": 555},
            }
        }
    )
    pubsub = _FakePubSub([_verified_envelope("g-backend", "9001")])
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())

    # DM raised Forbidden -> dropped; NO public guild fallback (privacy).
    assert dm_user.sent == []
    assert channel.sent == []


def test_registration_verified_dropped_when_user_unfetchable() -> None:
    channel = _FakeChannel(555)
    guild = _FakeGuild(222, name="Guild", channel=channel)
    # No cached user; fetch_user raises NotFound (HTTPException) -> dropped.
    bot = _FakeBot([guild], users={})
    backend = _FakeBackend(
        {
            222: {
                "guild_id": "g-backend",
                "discord_guild_id": 222,
                "settings": {"notify_channel_id": 555},
            }
        }
    )
    pubsub = _FakePubSub([_verified_envelope("g-backend", "9001")])
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())

    assert bot.fetch_calls == [9001]
    assert channel.sent == []


def test_registration_verified_bad_user_id_dropped() -> None:
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
    # Non-numeric discord_user_id -> int() raises ValueError -> dropped.
    pubsub = _FakePubSub([_verified_envelope("g-backend", "not-a-snowflake")])
    sub = NotificationSubscriber(bot, redis=_FakeRedis(pubsub), backend=backend)  # type: ignore[arg-type]

    asyncio.run(sub.run())

    assert bot.fetch_calls == []
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
            "registration_verified",
            {
                "discord_user_id": "9001",
                "riot_id": "Chama#Shasu",
                "region": "EUNE",
                "rank_text": "Unranked",
            },
        )
        is not None
    )
    assert (
        _build_embed(
            "result_recorded",
            {"match_label": "R1", "winner_name": "A", "loser_name": "B", "decided_by": "reported"},
        )
        is not None
    )
    assert _build_embed("unknown", {}) is None
