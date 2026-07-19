"""Scheduled tournaments — registration window, scheduler tick, announcements.

Offline tests over the shared fake session + a recording notification publisher:
the scheduler opens due registrations and fires one-time start announcements; the
join code is time-gated; create validates the schedule. Wall-clock SQL predicates
are re-checked in Python (see ``scheduler.run_scheduler_tick``), so the fake store
suffices here (the RLS-scoped cross-tenant scan is a CI/live concern). PLAN.md §3.2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from lol_schemas.enums import GameMode, TournamentStatus

from app.models import Tournament
from app.notifications import NotificationKind, RecordingNotificationPublisher
from app.tournaments import scheduler, service
from tests._fake_session import FakeSession

_GUILD = uuid.uuid4()
_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _session() -> FakeSession:
    s = FakeSession()
    s.current_guild = str(_GUILD)
    return s


def _tournament(**kw: object) -> Tournament:
    base: dict = {
        "id": uuid.uuid4(),
        "guild_id": _GUILD,
        "name": "Cup",
        "status": TournamentStatus.DRAFT,
        "game_mode": GameMode.SUMMONERS_RIFT_5V5,
        "max_teams": 16,
        "join_code": "ABCD2345",
    }
    base.update(kw)
    return Tournament(**base)


# --- scheduler tick --------------------------------------------------------- #


async def test_scheduler_opens_due_registration_and_announces() -> None:
    s = _session()
    t = _tournament(registration_opens_at=_NOW - timedelta(minutes=1))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    opened, started = await scheduler.run_scheduler_tick(s, pub, now=_NOW)

    assert (opened, started) == (1, 0)
    assert t.status == TournamentStatus.OPEN
    assert len(pub.published) == 1
    assert pub.published[0].kind == NotificationKind.TOURNAMENT_ANNOUNCED


async def test_scheduler_leaves_future_registration_closed() -> None:
    s = _session()
    t = _tournament(registration_opens_at=_NOW + timedelta(hours=1))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    opened, started = await scheduler.run_scheduler_tick(s, pub, now=_NOW)

    assert (opened, started) == (0, 0)
    assert t.status == TournamentStatus.DRAFT
    assert pub.published == []


async def test_scheduler_announces_start_once() -> None:
    s = _session()
    t = _tournament(status=TournamentStatus.OPEN, start_at=_NOW - timedelta(minutes=1))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    opened, started = await scheduler.run_scheduler_tick(s, pub, now=_NOW)
    assert (opened, started) == (0, 1)
    assert t.start_announced_at == _NOW

    # A second tick does NOT re-announce (start_announced_at guard).
    pub2 = RecordingNotificationPublisher()
    opened2, started2 = await scheduler.run_scheduler_tick(s, pub2, now=_NOW + timedelta(minutes=5))
    assert (opened2, started2) == (0, 0)
    assert pub2.published == []


# --- create validation ------------------------------------------------------ #


async def test_create_rejects_past_registration_time() -> None:
    s = _session()
    with pytest.raises(service.InvalidStateError):
        await service.create_tournament(
            s, name="Past", registration_opens_at=datetime(2000, 1, 1, tzinfo=UTC)
        )


async def test_create_rejects_start_before_registration() -> None:
    s = _session()
    future = datetime.now(tz=UTC) + timedelta(days=2)
    with pytest.raises(service.InvalidStateError):
        await service.create_tournament(
            s,
            name="Bad",
            registration_opens_at=future,
            start_at=future - timedelta(hours=1),
        )


# --- join gating ------------------------------------------------------------ #


async def test_join_rejected_before_registration_opens() -> None:
    s = _session()
    t = _tournament(
        game_mode=GameMode.ARAM_1V1,
        registration_opens_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    s.store[Tournament].append(t)

    with pytest.raises(service.RegistrationNotOpenError):
        await service.join_by_code(s, code="ABCD2345", user_id=uuid.uuid4())


async def test_join_rejected_after_start() -> None:
    s = _session()
    t = _tournament(
        status=TournamentStatus.OPEN,
        game_mode=GameMode.ARAM_1V1,
        start_at=datetime.now(tz=UTC) - timedelta(minutes=1),
    )
    s.store[Tournament].append(t)

    with pytest.raises(service.RegistrationClosedError):
        await service.join_by_code(s, code="ABCD2345", user_id=uuid.uuid4())
