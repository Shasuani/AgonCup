"""Global live-presence read API (PLAN.md §3.2 — cross-guild, field-whitelisted).

``GET /presence`` — any authenticated user — returns who is playing a platform
tournament match RIGHT NOW and on which Discord server. Deliberately NOT guild
scoped: the desktop bell shows global activity. The data is a non-PII projection
(display names + server name) held in Redis, so this never touches guild RLS.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import Principal, get_current_principal
from app.presence import service
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
