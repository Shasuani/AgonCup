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


# --- expiry sweep (abandoned tournaments) ----------------------------------- #
#
# A tournament that is created and then forgotten used to sit in every entrant's
# "my tournaments" list forever, looking live. ``run_expiry_tick`` closes those
# out. The reference date is a max() over three nullable columns, so it is
# re-checked in Python and the fake store suffices here (see the module docstring).

_EXPIRY_HOURS = 48


async def test_expiry_cancels_a_tournament_nobody_ever_finished() -> None:
    s = _session()
    t = _tournament(status=TournamentStatus.OPEN, created_at=_NOW - timedelta(hours=49))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 1
    assert t.status == TournamentStatus.CANCELLED
    assert len(pub.published) == 1
    assert pub.published[0].kind == NotificationKind.TOURNAMENT_ANNOUNCED


async def test_expiry_leaves_a_young_tournament_alone() -> None:
    s = _session()
    t = _tournament(status=TournamentStatus.OPEN, created_at=_NOW - timedelta(hours=47))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 0
    assert t.status == TournamentStatus.OPEN
    assert pub.published == []


async def test_expiry_spares_a_tournament_scheduled_for_the_future() -> None:
    """The whole point of measuring from the LAST scheduled moment.

    An admin sets a cup up weeks in advance: it is old by creation date and still
    perfectly alive. Measuring from ``created_at`` alone would cancel it two days
    after it was announced — the exact opposite of what the sweep is for.
    """
    s = _session()
    t = _tournament(
        status=TournamentStatus.OPEN,
        created_at=_NOW - timedelta(days=20),
        registration_opens_at=_NOW - timedelta(days=10),
        start_at=_NOW + timedelta(days=3),
    )
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 0
    assert t.status == TournamentStatus.OPEN


async def test_expiry_measures_from_the_scheduled_start_not_creation() -> None:
    s = _session()
    t = _tournament(
        status=TournamentStatus.CHECK_IN,
        created_at=_NOW - timedelta(days=20),
        start_at=_NOW - timedelta(hours=49),
    )
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 1
    assert t.status == TournamentStatus.CANCELLED


async def test_expiry_closes_a_bracket_abandoned_mid_flight() -> None:
    """RUNNING is swept too — a bracket nobody finished is the messiest leftover."""
    s = _session()
    t = _tournament(status=TournamentStatus.RUNNING, start_at=_NOW - timedelta(hours=72))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 1
    assert t.status == TournamentStatus.CANCELLED


async def test_expiry_never_touches_a_finished_tournament() -> None:
    """A completed tournament is the guild's record — it must survive forever."""
    s = _session()
    done = _tournament(status=TournamentStatus.COMPLETED, created_at=_NOW - timedelta(days=400))
    s.store[Tournament].append(done)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 0
    assert done.status == TournamentStatus.COMPLETED


async def test_expiry_announces_once_however_often_it_ticks() -> None:
    """The status flip takes the row out of the query — no repeat spam."""
    s = _session()
    t = _tournament(status=TournamentStatus.OPEN, created_at=_NOW - timedelta(days=5))
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    first = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)
    second = await scheduler.run_expiry_tick(
        s, pub, now=_NOW + timedelta(minutes=5), after_hours=_EXPIRY_HOURS
    )

    assert (first, second) == (1, 0)
    assert len(pub.published) == 1


async def test_expiry_skips_a_row_with_no_date_to_measure_from() -> None:
    """An unflushed row has no ``created_at`` yet — guess nothing, cancel nothing."""
    s = _session()
    t = _tournament(status=TournamentStatus.OPEN)
    assert t.created_at is None
    s.store[Tournament].append(t)
    pub = RecordingNotificationPublisher()

    cancelled = await scheduler.run_expiry_tick(s, pub, now=_NOW, after_hours=_EXPIRY_HOURS)

    assert cancelled == 0
    assert t.status == TournamentStatus.OPEN
