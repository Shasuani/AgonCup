"""Tests for the global live-presence store + read API (PLAN.md §3.2).

The Redis-facing store and the ``GET /presence`` aggregation are exercised here
with an in-memory fake Redis (offline). The DB-backed ``build_presence_entry`` /
orchestration hook is RLS-dependent and covered by the integration suite.
"""

from __future__ import annotations

import fnmatch
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.auth.deps import Principal
from app.presence import service
from app.presence.router import PresenceResponse, get_presence
from app.presence.service import PresenceEntry


class FakeRedis:
    """Minimal async Redis stand-in: set(ex)/get/delete/scan_iter."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_ex: int | None = None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.last_ex = ex

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def scan_iter(self, match: str | None = None) -> AsyncIterator[str]:
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


def _entry(match_id: str, players: list[str], started: datetime | None) -> PresenceEntry:
    return PresenceEntry(
        match_id=match_id,
        guild_name="Server One",
        tournament_name="Summer Cup",
        game_mode="summoners_rift_5v5",
        started_at=started,
        players=players,
    )


async def test_publish_then_list_round_trip() -> None:
    redis = FakeRedis()
    e = _entry("m1", ["Alice", "Bob"], datetime(2026, 7, 1, tzinfo=UTC))
    await service.publish_presence(redis, entry=e)
    out = await service.list_presence(redis)
    assert len(out) == 1
    assert out[0].match_id == "m1"
    assert out[0].players == ["Alice", "Bob"]
    assert out[0].guild_name == "Server One"


async def test_publish_uses_ttl() -> None:
    redis = FakeRedis()
    await service.publish_presence(redis, entry=_entry("m1", ["A"], None), ttl_s=1234)
    assert redis.last_ex == 1234
    # Default TTL when unspecified.
    await service.publish_presence(redis, entry=_entry("m2", ["B"], None))
    assert redis.last_ex == service.DEFAULT_TTL_S


async def test_clear_removes_entry() -> None:
    redis = FakeRedis()
    await service.publish_presence(redis, entry=_entry("m1", ["A"], None))
    await service.clear_presence(redis, match_id="m1")
    assert await service.list_presence(redis) == []


async def test_list_skips_malformed_values() -> None:
    redis = FakeRedis()
    await service.publish_presence(redis, entry=_entry("m1", ["A"], None))
    redis.store["lol:presence:match:junk"] = "not json{"
    out = await service.list_presence(redis)
    assert [e.match_id for e in out] == ["m1"]


async def test_list_sorts_newest_first() -> None:
    redis = FakeRedis()
    await service.publish_presence(
        redis, entry=_entry("old", ["A"], datetime(2026, 1, 1, tzinfo=UTC))
    )
    await service.publish_presence(
        redis, entry=_entry("new", ["B"], datetime(2026, 12, 1, tzinfo=UTC))
    )
    await service.publish_presence(redis, entry=_entry("none", ["C"], None))
    out = await service.list_presence(redis)
    assert [e.match_id for e in out] == ["new", "old", "none"]


async def test_router_aggregates_players_in_game() -> None:
    redis = FakeRedis()
    await service.publish_presence(redis, entry=_entry("m1", ["A", "B"], None))
    await service.publish_presence(redis, entry=_entry("m2", ["C", "D", "E"], None))
    principal = Principal(user_id="u1", guild_id=None, roles=())
    resp = await get_presence(principal, redis)
    assert isinstance(resp, PresenceResponse)
    assert resp.players_in_game == 5
    assert len(resp.entries) == 2


async def test_router_empty_when_nobody_playing() -> None:
    resp = await get_presence(Principal(user_id="u1", guild_id=None, roles=()), FakeRedis())
    assert resp.players_in_game == 0
    assert resp.entries == []
