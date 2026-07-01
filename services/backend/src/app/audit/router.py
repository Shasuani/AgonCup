"""Audit-log read API (PLAN.md §3.4 / §9).

A single operator-gated endpoint the admin SPA's audit viewer reads. The audit
trail is global (not per-guild RLS), so this runs on a plain ``SessionLocal`` and
gates to ``platform_operator`` at the route; an optional ``guild_id`` query param
narrows the feed. Writes happen inline in the acting services (e.g. a result
override), never here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from lol_schemas.enums import UserGuildRoleName
from pydantic import BaseModel

from app.audit import service
from app.auth.deps import Principal, require_role
from app.db import SessionLocal

router = APIRouter(prefix="/audit", tags=["audit"])

_OPERATOR = require_role(UserGuildRoleName.PLATFORM_OPERATOR)


class AuditEventResponse(BaseModel):
    id: str
    actor_id: str
    actor_name: str | None = None
    action: str
    entity_type: str
    entity_id: str
    guild_id: str | None = None
    detail: str | None = None
    created_at: str


@router.get(
    "/events",
    response_model=list[AuditEventResponse],
    summary="List recent audit events (platform_operator)",
)
async def list_audit_events(
    _principal: Annotated[Principal, Depends(_OPERATOR)],
    guild_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AuditEventResponse]:
    """Return recent audit events (newest first), optionally scoped to one guild."""
    guild_uuid = uuid.UUID(guild_id) if guild_id else None
    async with SessionLocal() as session:
        events = await service.list_audit_events(session, guild_id=guild_uuid, limit=limit)
    return [
        AuditEventResponse(
            id=e.id,
            actor_id=e.actor_id,
            actor_name=e.actor_name,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            guild_id=e.guild_id,
            detail=e.detail,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]
