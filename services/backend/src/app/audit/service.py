"""Audit-trail service: append privileged actions + read the log (PLAN.md §2 / §9).

The :class:`~app.models.AuditLog` is an append-only stream of who (actor) did what
(action) to which entity, and when. It is NOT under the standard per-guild RLS
policy (the model is role-aware: platform operators read all, guild admins read
their own guild), so both the write and the read run on a plain ``SessionLocal``
and scope at the route (operator-gated) / query (``guild_id`` filter).

The persisted row keeps a single ``entity`` string (the entity *type*, e.g.
``"match"``) plus an ``after`` JSON blob; this service packs the acted-upon id and
a human detail into ``after`` and unpacks them into the flat
:class:`AuditEventSummary` the SPA renders.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import AuditLog, User

logger = get_logger("app.audit.service")

#: Allowed ``actor_kind`` values (mirrors the model CHECK constraint).
ACTOR_PLATFORM = "platform"
ACTOR_GUILD = "guild"
ACTOR_SYSTEM = "system"


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    actor_id: uuid.UUID | None = None,
    actor_kind: str = ACTOR_GUILD,
    guild_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> AuditLog:
    """Append one audit event (never raises on a normal path; best-effort).

    ``entity_id``/``detail`` are packed into the ``after`` JSON so the flat SPA
    shape (``entity_type`` + ``entity_id`` + ``detail``) round-trips through the
    single-``entity`` column model. The caller owns the transaction/commit.
    """
    row = AuditLog(
        actor_id=actor_id,
        actor_kind=actor_kind,
        guild_id=guild_id,
        action=action,
        entity=entity_type,
        after={"entity_id": str(entity_id), "detail": detail},
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class AuditEventSummary:
    """One audit row flattened for the operator viewer (mirrors the SPA shape)."""

    id: str
    actor_id: str
    actor_name: str | None
    action: str
    entity_type: str
    entity_id: str
    guild_id: str | None
    detail: str | None
    created_at: datetime


async def list_audit_events(
    session: AsyncSession, *, guild_id: uuid.UUID | None = None, limit: int = 200
) -> list[AuditEventSummary]:
    """Return recent audit events (newest first), optionally scoped to one guild.

    Operator-gated at the route. Left-joins ``User`` to resolve the actor's display
    name; a ``system`` actor (no ``actor_id``) resolves to ``None``.
    """
    stmt = (
        select(AuditLog, User.display_name)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .order_by(AuditLog.ts.desc())
        .limit(limit)
    )
    if guild_id is not None:
        stmt = stmt.where(AuditLog.guild_id == guild_id)
    rows = (await session.execute(stmt)).all()
    events: list[AuditEventSummary] = []
    for row, actor_name in rows:
        after = row.after if isinstance(row.after, dict) else {}
        events.append(
            AuditEventSummary(
                id=str(row.id),
                actor_id=str(row.actor_id) if row.actor_id else "",
                actor_name=actor_name,
                action=row.action,
                entity_type=row.entity or "",
                entity_id=str(after.get("entity_id") or ""),
                guild_id=str(row.guild_id) if row.guild_id else None,
                detail=after.get("detail"),
                created_at=row.ts,
            )
        )
    return events


__all__ = [
    "ACTOR_GUILD",
    "ACTOR_PLATFORM",
    "ACTOR_SYSTEM",
    "AuditEventSummary",
    "list_audit_events",
    "record_audit",
]
