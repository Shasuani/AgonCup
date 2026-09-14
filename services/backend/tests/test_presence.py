"""Tests for the global live-presence store + read API (PLAN.md §3.2).

The Redis-facing store and the ``GET /presence`` aggregation are exercised here
with an in-memory fake Redis (offline). The DB-backed ``build_presence_entry`` /
orchestration hook is RLS-dependent and covered by the integration suite.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.auth.deps import Principal
from app.presence import live, service
from app.presence.router import PresenceResponse, get_live_board, get_presence
from app.presence.service import PresenceEntry

_MATCH = uuid.uuid4()


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


# ---------------------------------------------------------------------------
# Live scoreboard (watch a game) — app.presence.live
# ---------------------------------------------------------------------------
def _client_player(riot_id: str, **over: object) -> dict[str, object]:
    """One player exactly as the desktop sends it (camelCase)."""
    return {
        "riotId": riot_id,
        "championKey": "MissFortune",
        "championName": "Miss Fortune",
        "team": "ORDER",
        "level": 9,
        "isDead": False,
        "respawnTimer": 0,
        "items": [1055, 0, 3006, 0, 0, 0, 3340],
        "kills": 4,
        "deaths": 2,
        "assists": 7,
        "creepScore": 110,
        "wardScore": 6.4,
        "spell1Key": "SummonerFlash",
        "spell2Key": "SummonerHeal",
        **over,
    }


def _snapshot(game_time: float, players: list[dict[str, object]]) -> dict[str, object]:
    return {"gameTime": game_time, "gameMode": "CLASSIC", "players": players}


async def _live(redis: FakeRedis, match_id: str) -> None:
    await service.publish_presence(redis, entry=_entry(match_id, ["A"], None))


def test_live_snapshot_rejects_malformed_and_oversized_input() -> None:
    assert live.parse_client_snapshot(None) is None
    assert live.parse_client_snapshot({"players": []}) is None  # no game time
    assert live.parse_client_snapshot(_snapshot(60, [_client_player("A#1")] * 11)) is None
    assert live.parse_client_snapshot(_snapshot(60, [_client_player("A#1", level=99)])) is None
    assert live.parse_client_snapshot(_snapshot(60, [_client_player("A#1", items=[1] * 8)])) is None
    assert (
        live.parse_client_snapshot(_snapshot(60, [_client_player("A#1", championKey="<img>")]))
        is None
    )
    ok = live.parse_client_snapshot(_snapshot(60, [_client_player("A#1", currentGold=999)]))
    assert ok is not None and ok.players[0].creep_score == 110


def test_live_board_shows_display_names_never_riot_ids() -> None:
    snap = live.parse_client_snapshot(
        _snapshot(
            754,
            [
                _client_player("Faker#KR1"),
                _client_player("Stranger#EUW", isDead=True, respawnTimer=17, items=[3340]),
            ],
        )
    )
    assert snap is not None
    board = live.build_live_board(
        match_id="m1",
        snapshot=snap,
        names_by_riot_id={"faker#kr1": "Alice"},
        now=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert [p.name for p in board.players] == ["Alice", live.UNKNOWN_PLAYER]
    assert board.players[1].items == [3340, 0, 0, 0, 0, 0, 0]
    assert board.players[1].respawn_timer == 17
    dumped = board.model_dump_json()
    assert "Faker" not in dumped and "Stranger" not in dumped


async def test_live_board_is_stored_only_for_live_games_and_never_rolled_back() -> None:
    redis = FakeRedis()
    assert await live.wants_board(redis, match_id="m1", game_time=60) is False  # not in presence
    await _live(redis, "m1")
    assert await live.wants_board(redis, match_id="m1", game_time=60) is True

    snap = live.parse_client_snapshot(_snapshot(60, [_client_player("A#1")]))
    assert snap is not None
    board = live.build_live_board(
        match_id="m1", snapshot=snap, names_by_riot_id={}, now=datetime.now(UTC)
    )
    await live.put_live_board(redis, board=board)
    assert redis.last_ex == live.LIVE_TTL_S
    # the other nine clients' copies of the same moment, and an older one
    assert await live.wants_board(redis, match_id="m1", game_time=62) is False
    assert await live.wants_board(redis, match_id="m1", game_time=30) is False
    assert await live.wants_board(redis, match_id="m1", game_time=64) is True


async def test_ingest_live_snapshot_names_players_from_the_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    await _live(redis, str(_MATCH))

    async def roster(session: object, *, match_id: uuid.UUID) -> dict[str, str]:
        assert match_id == _MATCH
        return {"faker#kr1": "Alice"}

    monkeypatch.setattr(live, "roster_names_by_riot_id", roster)
    raw = _snapshot(120, [_client_player("Faker#KR1")])
    assert await live.ingest_live_snapshot(None, redis, match_id=_MATCH, raw=raw) is True  # type: ignore[arg-type]
    assert await live.ingest_live_snapshot(None, redis, match_id=_MATCH, raw=raw) is False  # type: ignore[arg-type]
    assert await live.ingest_live_snapshot(None, redis, match_id=_MATCH, raw={"x": 1}) is False  # type: ignore[arg-type]
    board = await live.get_live_board(redis, match_id=str(_MATCH))
    assert board is not None and board.players[0].name == "Alice"


async def test_live_board_endpoint() -> None:
    redis = FakeRedis()
    principal = Principal(user_id="u1", guild_id=None, roles=())
    with pytest.raises(HTTPException) as not_live:
        await get_live_board(str(_MATCH), principal, redis)
    assert not_live.value.status_code == 404

    await _live(redis, str(_MATCH))
    with pytest.raises(HTTPException) as nothing_yet:
        await get_live_board(str(_MATCH), principal, redis)
    assert nothing_yet.value.status_code == 404

    snap = live.parse_client_snapshot(_snapshot(300, [_client_player("A#1")]))
    assert snap is not None
    await live.put_live_board(
        redis,
        board=live.build_live_board(
            match_id=str(_MATCH), snapshot=snap, names_by_riot_id={}, now=datetime.now(UTC)
        ),
    )
    board = await get_live_board(str(_MATCH), principal, redis)
    assert board.game_time == 300
    assert board.players[0].champion_key == "MissFortune"


async def test_clearing_presence_also_drops_the_live_scoreboard() -> None:
    redis = FakeRedis()
    await _live(redis, "m1")
    snap = live.parse_client_snapshot(_snapshot(60, [_client_player("A#1")]))
    assert snap is not None
    await live.put_live_board(
        redis,
        board=live.build_live_board(
            match_id="m1", snapshot=snap, names_by_riot_id={}, now=datetime.now(UTC)
        ),
    )
    await service.clear_presence(redis, match_id="m1")
    assert await live.get_live_board(redis, match_id="m1") is None
    assert await service.list_presence(redis) == []
