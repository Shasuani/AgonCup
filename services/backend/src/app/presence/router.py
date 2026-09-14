"""Global live-presence read API (PLAN.md §3.2 — cross-guild, field-whitelisted).

``GET /presence`` — any authenticated user — returns who is playing a platform
tournament match RIGHT NOW and on which Discord server. Deliberately NOT guild
scoped: the desktop bell shows global activity. The data is a non-PII projection
(display names + server name) held in Redis, so this never touches guild RLS.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import Principal, get_current_principal
from app.presence import live, service
from app.presence.live import LiveBoard
from app.presence.service import PresenceEntry

router = APIRouter(prefix="/presence", tags=["presence"])


class PresenceResponse(BaseModel):
    players_in_game: int
    entries: list[PresenceEntry]


def get_presence_redis() -> Any:
    """Dependency yielding the shared async Redis client (overridable in tests)."""
    from app.realtime.redis_client import get_redis  # noqa: PLC0415 - deferred, IO-free import

    return get_redis()


@router.get("", response_model=PresenceResponse, summary="Who is playing right now (global)")
async def get_presence(
    _principal: Annotated[Principal, Depends(get_current_principal)],
    redis: Annotated[Any, Depends(get_presence_redis)],
) -> PresenceResponse:
    entries = await service.list_presence(redis)
    return PresenceResponse(
        players_in_game=sum(len(e.players) for e in entries),
        entries=entries,
    )


@router.get(
    "/live/{match_id}",
    response_model=LiveBoard,
    summary="The live scoreboard of a game in progress (global, display-only)",
)
async def get_live_board(
    match_id: str,
    _principal: Annotated[Principal, Depends(get_current_principal)],
    redis: Annotated[Any, Depends(get_presence_redis)],
) -> LiveBoard:
    """Same audience as ``GET /presence``: any signed-in player can watch a game
    presence lists. 404 until a client has sent a board, and once it expires."""
    if await redis.get(f"{service.PRESENCE_KEY_PREFIX}{match_id}") is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game is not live")
    board = await live.get_live_board(redis, match_id=match_id)
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no live scoreboard yet")
    return board
