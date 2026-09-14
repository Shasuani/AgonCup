"""Global live-presence store — "who's playing right now, and on which server".

Presence is ephemeral cross-guild state, so it lives in **Redis**, not the DB:
a single keyspace is inherently cross-tenant (no per-guild RLS to fight) and a
short TTL means a crashed client self-heals. Only NON-PII display data is
stored — player *display names* and the *guild (server) name* — never Riot IDs or
PUUIDs (PLAN.md §3.2 field-whitelisted projection, applied to live presence).

Written when a match lobby enters ``IN_GAME`` and cleared when it ends
(``app.orchestration.service.ingest_lobby_state``); read by the desktop bell via
``GET /api/v1/presence``. Every write is best-effort: a Redis failure must never
break orchestration.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Guild, Match, TeamMember, Tournament, User

log = get_logger("app.presence")

#: Redis key namespace for one in-game match's presence entry.
PRESENCE_KEY_PREFIX = "lol:presence:match:"
#: Redis key namespace for a live game's scoreboard (see :mod:`app.presence.live`);
#: it lives and dies with the presence entry, so it is defined next to it.
LIVE_KEY_PREFIX = "lol:live:match:"
#: Default TTL: a League game rarely exceeds ~90 min; a stale entry self-expires
#: even if the "game ended" clear is lost (best-effort orchestration).
DEFAULT_TTL_S = 5400


def _key(match_id: str) -> str:
    return f"{PRESENCE_KEY_PREFIX}{match_id}"


class PresenceEntry(BaseModel):
    """One live in-game match, projected to non-PII display fields."""

    match_id: str
    guild_name: str
    tournament_name: str
    game_mode: str
    started_at: datetime | None = None
    players: list[str]


async def publish_presence(redis: Any, *, entry: PresenceEntry, ttl_s: int = DEFAULT_TTL_S) -> None:
    """Write (or refresh) a match's presence entry with a TTL."""
    await redis.set(_key(entry.match_id), entry.model_dump_json(), ex=ttl_s)


async def clear_presence(redis: Any, *, match_id: str) -> None:
    """Remove a match's presence entry and live scoreboard (game ended/aborted)."""
    await redis.delete(_key(match_id))
    await redis.delete(f"{LIVE_KEY_PREFIX}{match_id}")


async def list_presence(redis: Any) -> list[PresenceEntry]:
    """Return every live presence entry, newest first (malformed rows skipped)."""
    entries: list[PresenceEntry] = []
    async for key in redis.scan_iter(match=f"{PRESENCE_KEY_PREFIX}*"):
        raw = await redis.get(key)
        if raw is None:
            continue
        try:
            entries.append(PresenceEntry.model_validate_json(raw))
        except (ValueError, json.JSONDecodeError) as exc:  # tolerate a bad/legacy value
            log.warning("presence.malformed_entry", key=str(key), error=str(exc))
    # Newest first; entries without a start time sort last (datetime.min proxy).
    entries.sort(key=lambda e: (e.started_at is not None, e.started_at), reverse=True)
    return entries


async def _match_player_names(session: AsyncSession, match: Match) -> list[str]:
    """Display names of every player on either side of a match (RLS-scoped read)."""
    team_ids = [t for t in (match.team_a, match.team_b) if t is not None]
    if not team_ids:
        return []
    rows = (
        await session.execute(
            select(User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id.in_(team_ids))
            .order_by(User.display_name)
        )
    ).scalars()
    return list(rows)


async def build_presence_entry(
    session: AsyncSession, *, match_id: uuid.UUID, started_at: datetime | None
) -> PresenceEntry | None:
    """Assemble a :class:`PresenceEntry` for an in-game match (RLS-scoped).

    Runs on the orchestration session (which has the acting guild set), so the
    guild/tournament/team reads are naturally scoped. Returns ``None`` if the
    match/tournament/guild can't be resolved (nothing to publish).
    """
    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        return None
    tournament = (
        await session.execute(select(Tournament).where(Tournament.id == match.tournament_id))
    ).scalar_one_or_none()
    guild = (
        await session.execute(select(Guild).where(Guild.id == match.guild_id))
    ).scalar_one_or_none()
    if tournament is None or guild is None:
        return None
    return PresenceEntry(
        match_id=str(match_id),
        guild_name=guild.name,
        tournament_name=tournament.name,
        game_mode=tournament.game_mode.value
        if hasattr(tournament.game_mode, "value")
        else str(tournament.game_mode),
        started_at=started_at,
        players=await _match_player_names(session, match),
    )


async def refresh_presence_for_match(
    session: AsyncSession,
    redis: Any,
    *,
    match_id: uuid.UUID,
    started_at: datetime | None = None,
) -> None:
    """Best-effort: publish a match's presence entry (no-op on any failure)."""
    try:
        entry = await build_presence_entry(session, match_id=match_id, started_at=started_at)
        if entry is not None:
            await publish_presence(redis, entry=entry)
    except Exception as exc:  # noqa: BLE001 - presence is best-effort, never fatal
        log.warning("presence.refresh_failed", match_id=str(match_id), error=str(exc))


async def clear_presence_for_match(redis: Any, *, match_id: uuid.UUID) -> None:
    """Best-effort presence clear (no-op on any failure)."""
    try:
        await clear_presence(redis, match_id=str(match_id))
    except Exception as exc:  # noqa: BLE001 - best-effort
        log.warning("presence.clear_failed", match_id=str(match_id), error=str(exc))
