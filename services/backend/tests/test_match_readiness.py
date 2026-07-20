"""Match-ready gate — mark-ready, captaincy resolution, arm + forfeit tick.

Offline tests over the shared fake session + a recording notification publisher
(edge-cases §8.5). Wall-clock / IS NULL SQL predicates are re-checked in Python
inside ``readiness.run_readiness_tick`` (mirrors ``scheduler.run_scheduler_tick``),
so the fake store suffices; the RLS cross-tenant scan is a CI/live concern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from lol_schemas.enums import (
    MatchStatus,
    ResultDecidedBy,
    TeamMemberRole,
    TeamMemberStatus,
)

from app.models import Match, MatchResult, Team, TeamMember
from app.notifications import NotificationKind, RecordingNotificationPublisher
from app.tournaments import readiness
from tests._fake_session import FakeSession

_GUILD = uuid.uuid4()
_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
_WINDOW = 300


def _session() -> FakeSession:
    s = FakeSession()
    s.current_guild = str(_GUILD)
    return s


def _match(**kw: object) -> Match:
    base: dict = {
        "id": uuid.uuid4(),
        "guild_id": _GUILD,
        "tournament_id": uuid.uuid4(),
        "round": 0,
        "bracket_slot": 0,
        "team_a": uuid.uuid4(),
        "team_b": uuid.uuid4(),
        "winner_team_id": None,
        "status": MatchStatus.PENDING,
        "wins_a": 0,
        "wins_b": 0,
        "scored_game_ids": [],
        "ready_deadline_at": None,
        "team_a_ready_at": None,
        "team_b_ready_at": None,
    }
    base.update(kw)
    return Match(**base)


def _captain(team_id: uuid.UUID, user_id: uuid.UUID) -> TeamMember:
    return TeamMember(
        team_id=team_id,
        user_id=user_id,
        guild_id=_GUILD,
        role=TeamMemberRole.CAPTAIN,
        status=TeamMemberStatus.ACTIVE,
    )


# --- mark_team_ready --------------------------------------------------------
async def test_mark_ready_one_side_then_both() -> None:
    s = _session()
    m = _match()
    s.store[Match].append(m)

    first = await readiness.mark_team_ready(s, match_id=m.id, team_id=m.team_a, now=_NOW)
    assert first.side == "team_a"
    assert first.both_ready is False
    assert m.team_a_ready_at == _NOW

    second = await readiness.mark_team_ready(s, match_id=m.id, team_id=m.team_b, now=_NOW)
    assert second.side == "team_b"
    assert second.both_ready is True
    assert m.ready_deadline_at is None  # disarmed once both readied


async def test_mark_ready_idempotent() -> None:
    s = _session()
    m = _match(team_a_ready_at=_NOW)
    s.store[Match].append(m)
    out = await readiness.mark_team_ready(
        s, match_id=m.id, team_id=m.team_a, now=_NOW + timedelta(minutes=1)
    )
    assert out.already is True
    assert m.team_a_ready_at == _NOW  # not overwritten


async def test_mark_ready_rejects_unknown_team() -> None:
    s = _session()
    m = _match()
    s.store[Match].append(m)
    with pytest.raises(readiness.TeamNotInMatchError):
        await readiness.mark_team_ready(s, match_id=m.id, team_id=uuid.uuid4(), now=_NOW)


async def test_mark_ready_rejects_completed_match() -> None:
    s = _session()
    m = _match(status=MatchStatus.COMPLETED)
    s.store[Match].append(m)
    with pytest.raises(readiness.MatchNotReadyableError):
        await readiness.mark_team_ready(s, match_id=m.id, team_id=m.team_a, now=_NOW)


# --- resolve_ready_team (captaincy) -----------------------------------------
async def test_resolve_ready_team_returns_captained_side() -> None:
    s = _session()
    m = _match()
    user = uuid.uuid4()
    s.store[Match].append(m)
    s.store[TeamMember].append(_captain(m.team_b, user))
    got = await readiness.resolve_ready_team(s, match_id=m.id, user_id=user)
    assert got == m.team_b


async def test_resolve_ready_team_rejects_non_captain() -> None:
    s = _session()
    m = _match()
    s.store[Match].append(m)
    with pytest.raises(readiness.NotTeamCaptainError):
        await readiness.resolve_ready_team(s, match_id=m.id, user_id=uuid.uuid4())


# --- run_readiness_tick: arm ------------------------------------------------
async def test_tick_arms_playable_match() -> None:
    s = _session()
    m = _match()
    s.store[Match].append(m)
    s.store[Team].extend(
        [
            Team(id=m.team_a, guild_id=_GUILD, name="Alpha"),
            Team(id=m.team_b, guild_id=_GUILD, name="Bravo"),
        ]
    )
    pub = RecordingNotificationPublisher()

    armed, forfeited = await readiness.run_readiness_tick(s, pub, now=_NOW, window_seconds=_WINDOW)
    assert (armed, forfeited) == (1, 0)
    assert m.ready_deadline_at == _NOW + timedelta(seconds=_WINDOW)
    assert [e.payload.kind for e in pub.published] == [NotificationKind.TOURNAMENT_ANNOUNCED]


async def test_tick_does_not_arm_incomplete_match() -> None:
    s = _session()
    m = _match(team_b=None)  # only one team present
    s.store[Match].append(m)
    pub = RecordingNotificationPublisher()
    armed, _ = await readiness.run_readiness_tick(s, pub, now=_NOW, window_seconds=_WINDOW)
    assert armed == 0
    assert m.ready_deadline_at is None


# --- run_readiness_tick: forfeit --------------------------------------------
async def test_tick_forfeits_unready_side() -> None:
    s = _session()
    deadline = _NOW - timedelta(seconds=1)
    m = _match(ready_deadline_at=deadline, team_a_ready_at=_NOW - timedelta(minutes=2))
    s.store[Match].append(m)
    s.store[Team].extend(
        [
            Team(id=m.team_a, guild_id=_GUILD, name="Alpha"),
            Team(id=m.team_b, guild_id=_GUILD, name="Bravo"),
        ]
    )
    pub = RecordingNotificationPublisher()

    armed, forfeited = await readiness.run_readiness_tick(s, pub, now=_NOW, window_seconds=_WINDOW)
    assert (armed, forfeited) == (0, 1)
    assert m.winner_team_id == m.team_a
    assert m.status == MatchStatus.COMPLETED
    result = s.store[MatchResult][0]
    assert result.decided_by == ResultDecidedBy.WALKOVER
    assert m.ready_deadline_at is None


async def test_tick_rearms_when_neither_ready() -> None:
    s = _session()
    deadline = _NOW - timedelta(seconds=1)
    m = _match(ready_deadline_at=deadline)  # nobody readied
    s.store[Match].append(m)
    pub = RecordingNotificationPublisher()

    armed, forfeited = await readiness.run_readiness_tick(s, pub, now=_NOW, window_seconds=_WINDOW)
    assert (armed, forfeited) == (0, 0)
    assert m.ready_deadline_at == _NOW + timedelta(seconds=_WINDOW)  # extended
    assert m.winner_team_id is None
    assert not s.store[MatchResult]  # no forfeit recorded
