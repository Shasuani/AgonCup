"""Tests for the audit-trail service (record + read).

Offline: a tiny fake session captures the appended row (record_audit) and replays
a canned joined result (list_audit_events), so we assert the entity/detail packing
round-trips through the single-``entity`` column model into the flat SPA shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.audit import service
from app.models import AuditLog


class _AddSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _Res:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _ReadSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return _Res(self._rows)


@pytest.mark.asyncio
async def test_record_audit_packs_entity_and_detail() -> None:
    session = _AddSession()
    actor, guild, match = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    row = await service.record_audit(
        session,  # type: ignore[arg-type]
        action="result.override",
        entity_type="match",
        entity_id=match,
        actor_id=actor,
        actor_kind=service.ACTOR_GUILD,
        guild_id=guild,
        detail="forfeit — no-show",
    )

    assert session.added == [row]
    assert row.action == "result.override"
    assert row.entity == "match"
    assert row.actor_kind == "guild"
    assert row.after == {"entity_id": str(match), "detail": "forfeit — no-show"}
    assert row.actor_id == actor
    assert row.guild_id == guild


@pytest.mark.asyncio
async def test_list_audit_events_flattens_the_row() -> None:
    match = uuid.uuid4()
    row = AuditLog(
        actor_id=uuid.uuid4(),
        actor_kind="guild",
        guild_id=uuid.uuid4(),
        action="result.override",
        entity="match",
        after={"entity_id": str(match), "detail": "coach call"},
    )
    row.id = uuid.uuid4()
    row.ts = datetime(2026, 7, 1, tzinfo=UTC)
    session = _ReadSession([(row, "Alice")])

    events = await service.list_audit_events(session)  # type: ignore[arg-type]

    assert len(events) == 1
    e = events[0]
    assert e.action == "result.override"
    assert e.entity_type == "match"
    assert e.entity_id == str(match)
    assert e.detail == "coach call"
    assert e.actor_name == "Alice"


@pytest.mark.asyncio
async def test_list_audit_events_handles_system_actor() -> None:
    row = AuditLog(
        actor_id=None,
        actor_kind="system",
        guild_id=None,
        action="result.quorum",
        entity="match",
        after={"entity_id": str(uuid.uuid4()), "detail": None},
    )
    row.id = uuid.uuid4()
    row.ts = datetime(2026, 7, 1, tzinfo=UTC)
    session = _ReadSession([(row, None)])

    events = await service.list_audit_events(session)  # type: ignore[arg-type]

    assert events[0].actor_id == ""
    assert events[0].actor_name is None
    assert events[0].guild_id is None
