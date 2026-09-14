"""Live scoreboard — watch a running tournament game from the desktop client.

Every in-game desktop client reads the documented Live Client Data API
(``:2999/liveclientdata/allgamedata``) and sends a small snapshot over its match
WebSocket every few seconds: champion, level, items, K/D/A, CS, wards and spells
for all ten players — exactly what TAB shows everyone in the game, nothing more
(no gold, no map position). Display-only: it never feeds a result.

Stored like presence (PLAN.md §3.2): ephemeral, cross-guild, in Redis, with a
short TTL so a game whose clients all vanished simply drops off. And projected
the same way — each player is shown by their platform DISPLAY NAME; the Riot ID
the client sends is used only to find that name and is never stored.

A game is watchable only while it is listed in presence (the lobby reached
``IN_GAME``), which is also the list the client offers to watch.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import ClientSession, RiotAccount, User
from app.presence.service import LIVE_KEY_PREFIX, PRESENCE_KEY_PREFIX

log = get_logger("app.presence.live")

#: A board nobody refreshed for this long is gone (clients send every ~5s).
LIVE_TTL_S = 90
#: Up to ten clients send the same game; keep one board per this much GAME time.
MIN_GAME_SECONDS_BETWEEN_WRITES = 4.0
#: Shown for a player the roster cannot name (someone outside the match).
UNKNOWN_PLAYER = "Player"

_KEY_PATTERN = r"^[A-Za-z0-9]*$"


def _key(match_id: str) -> str:
    return f"{LIVE_KEY_PREFIX}{match_id}"


class _FromClient(BaseModel):
    """The desktop's camelCase wire shape; unknown fields are ignored, not trusted."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class ClientLivePlayer(_FromClient):
    riot_id: str = Field(default="", max_length=64)
    champion_key: str = Field(default="", max_length=40, pattern=_KEY_PATTERN)
    champion_name: str = Field(default="", max_length=40)
    team: str = Field(default="", max_length=8)
    level: int = Field(default=0, ge=0, le=30)
    is_dead: bool = False
    respawn_timer: float = Field(default=0, ge=0, le=600)
    items: list[int] = Field(default_factory=list, max_length=7)
    kills: int = Field(default=0, ge=0, le=999)
    deaths: int = Field(default=0, ge=0, le=999)
    assists: int = Field(default=0, ge=0, le=999)
    creep_score: int = Field(default=0, ge=0, le=9999)
    ward_score: float = Field(default=0, ge=0, le=9999)
    spell1_key: str | None = Field(default=None, max_length=40, pattern=_KEY_PATTERN)
    spell2_key: str | None = Field(default=None, max_length=40, pattern=_KEY_PATTERN)


class ClientLiveSnapshot(_FromClient):
    game_time: float = Field(ge=0, le=86_400)
    game_mode: str = Field(default="", max_length=24)
    players: list[ClientLivePlayer] = Field(max_length=10)


class LivePlayer(BaseModel):
    """One scoreboard row as viewers get it — a display name, never a Riot ID."""

    name: str
    champion_key: str
    champion_name: str
    #: ``ORDER`` = blue side, ``CHAOS`` = red side.
    team: str
    level: int
    is_dead: bool
    respawn_timer: float
    items: list[int]
    kills: int
    deaths: int
    assists: int
    creep_score: int
    ward_score: float
    spell1_key: str | None = None
    spell2_key: str | None = None


class LiveBoard(BaseModel):
    """The latest live scoreboard of one game."""

    match_id: str
    game_time: float
    game_mode: str
    updated_at: datetime
    players: list[LivePlayer]


def parse_client_snapshot(raw: Any) -> ClientLiveSnapshot | None:
    """Validate a client's snapshot; anything malformed or oversized is dropped."""
    try:
        return ClientLiveSnapshot.model_validate(raw)
    except ValidationError:
        return None


def build_live_board(
    *,
    match_id: str,
    snapshot: ClientLiveSnapshot,
    names_by_riot_id: Mapping[str, str],
    now: datetime,
) -> LiveBoard:
    """Project a client snapshot to the stored board (Riot IDs swapped for names)."""
    return LiveBoard(
        match_id=match_id,
        game_time=snapshot.game_time,
        game_mode=snapshot.game_mode,
        updated_at=now,
        players=[
            LivePlayer(
                name=names_by_riot_id.get(p.riot_id.casefold(), UNKNOWN_PLAYER),
                champion_key=p.champion_key,
                champion_name=p.champion_name,
                team=p.team,
                level=p.level,
                is_dead=p.is_dead,
                respawn_timer=p.respawn_timer if p.is_dead else 0,
                items=(p.items + [0] * 7)[:7],
                kills=p.kills,
                deaths=p.deaths,
                assists=p.assists,
                creep_score=p.creep_score,
                ward_score=p.ward_score,
                spell1_key=p.spell1_key,
                spell2_key=p.spell2_key,
            )
            for p in snapshot.players
        ],
    )


async def get_live_board(redis: Any, *, match_id: str) -> LiveBoard | None:
    """The latest board of a game, or ``None`` (none sent yet, or it expired)."""
    raw = await redis.get(_key(match_id))
    if raw is None:
        return None
    try:
        return LiveBoard.model_validate_json(raw)
    except ValueError as exc:
        log.warning("live.malformed_board", match_id=match_id, error=str(exc))
        return None


async def wants_board(redis: Any, *, match_id: str, game_time: float) -> bool:
    """Is a board at ``game_time`` worth storing for this game?

    Only while the game is live (listed in presence), and only when it is at least
    :data:`MIN_GAME_SECONDS_BETWEEN_WRITES` of game time newer than the stored one:
    ten clients send the same game, and a lagging or replayed client must not roll
    the board back. Cheap (Redis only), so it runs before any database read.
    """
    if await redis.get(f"{PRESENCE_KEY_PREFIX}{match_id}") is None:
        return False
    current = await get_live_board(redis, match_id=match_id)
    return current is None or game_time >= current.game_time + MIN_GAME_SECONDS_BETWEEN_WRITES


async def put_live_board(redis: Any, *, board: LiveBoard) -> None:
    """Store a game's board with the short live TTL."""
    await redis.set(_key(board.match_id), board.model_dump_json(), ex=LIVE_TTL_S)


async def roster_names_by_riot_id(session: AsyncSession, *, match_id: uuid.UUID) -> dict[str, str]:
    """``name#tag`` (casefolded) -> display name for everyone on the match roster.

    RLS-scoped: runs on the WebSocket frame's tenant session.
    """
    roster: Sequence[ClientSession] = (
        (await session.execute(select(ClientSession).where(ClientSession.match_id == match_id)))
        .scalars()
        .all()
    )
    if not roster:
        return {}
    accounts = (
        await session.execute(
            select(RiotAccount).where(RiotAccount.puuid.in_([s.puuid for s in roster]))
        )
    ).scalars()
    riot_id_by_puuid = {
        a.puuid: f"{a.riot_id_game_name}#{a.riot_id_tag_line}".casefold()
        for a in accounts
        if a.riot_id_game_name and a.riot_id_tag_line
    }
    users = (
        await session.execute(select(User).where(User.id.in_([s.user_id for s in roster])))
    ).scalars()
    name_by_user = {u.id: u.display_name for u in users}
    return {
        riot_id_by_puuid[s.puuid]: name_by_user[s.user_id]
        for s in roster
        if s.puuid in riot_id_by_puuid and s.user_id in name_by_user
    }


async def ingest_live_snapshot(
    session: AsyncSession, redis: Any, *, match_id: uuid.UUID, raw: Any
) -> bool:
    """Validate, name and store one client's live snapshot. Returns whether it was stored."""
    snapshot = parse_client_snapshot(raw)
    if snapshot is None:
        return False
    if not await wants_board(redis, match_id=str(match_id), game_time=snapshot.game_time):
        return False
    board = build_live_board(
        match_id=str(match_id),
        snapshot=snapshot,
        names_by_riot_id=await roster_names_by_riot_id(session, match_id=match_id),
        now=datetime.now(UTC),
    )
    await put_live_board(redis, board=board)
    return True
