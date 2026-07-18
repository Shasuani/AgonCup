"""The "smart" tournament join code — enrolment shape follows the game mode.

``tournaments.service.join_by_code`` redeems the single code an admin hands out.
For **solo** modes (``ARAM_1V1``, team_size 1) it enrols the caller as an
individual participant; for **team** modes (``SUMMONERS_RIFT_5V5``, team_size 5)
the caller must be a team **captain** and their team is registered. These are the
offline branch/guard tests (the RLS-scoped row visibility is proven in the
Postgres integration suite). See PLAN.md §3.1.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import GameMode, TournamentStatus

from app.models import Registration, Team, Tournament, TournamentParticipant
from app.tournaments import service
from tests._fake_session import FakeSession

_CODE = "ABCD2345"  # already-normalized join code (alphabet-valid)


def _tournament(*, guild_id: uuid.UUID, game_mode: GameMode) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Cup",
        game_mode=game_mode,
        status=TournamentStatus.OPEN,
        max_teams=16,
        join_code=_CODE,
    )


def _team(*, guild_id: uuid.UUID, captain_user_id: uuid.UUID, name: str = "Alpha") -> Team:
    return Team(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name=name,
        captain_user_id=captain_user_id,
    )


def _seed(session: FakeSession, *objs: object) -> None:
    session.current_guild = None
    for obj in objs:
        session.store[type(obj)].append(obj)


async def test_solo_mode_enrols_the_individual() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _seed(session, _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1))
    session.current_guild = str(guild_id)

    _, enrolment = await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert isinstance(enrolment, TournamentParticipant)
    assert enrolment.user_id == user_id
    assert len(session.store[TournamentParticipant]) == 1
    assert session.store[Registration] == []


async def test_team_mode_registers_the_captains_team() -> None:
    guild_id, captain = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    team = _team(guild_id=guild_id, captain_user_id=captain)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )
    session.store[Team].append(team)

    _, reg = await service.join_by_code(session, code=_CODE, user_id=captain)

    assert isinstance(reg, Registration)
    assert reg.team_id == team.id
    assert len(session.store[Registration]) == 1
    assert session.store[TournamentParticipant] == []


async def test_team_mode_rejects_a_cross_guild_code() -> None:
    """A team can only enter a tournament in its OWN guild. A code resolving to a
    foreign-guild tournament (possible only if RLS is bypassed) reads as invalid —
    a clean 404, not a composite-FK 500."""
    tour_guild, team_guild, captain = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(team_guild)
    session.store[Tournament].append(
        _tournament(guild_id=tour_guild, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )
    session.store[Team].append(_team(guild_id=team_guild, captain_user_id=captain))

    with pytest.raises(service.InvalidJoinCodeError):
        await service.join_by_code(session, code=_CODE, user_id=captain)


async def test_team_mode_without_a_captained_team_is_rejected() -> None:
    guild_id, loner = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )

    with pytest.raises(service.NoCaptainTeamError):
        await service.join_by_code(session, code=_CODE, user_id=loner)


async def test_team_mode_ambiguous_when_captain_of_several_teams() -> None:
    guild_id, captain = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )
    session.store[Team].append(_team(guild_id=guild_id, captain_user_id=captain, name="Alpha"))
    session.store[Team].append(_team(guild_id=guild_id, captain_user_id=captain, name="Beta"))

    with pytest.raises(service.AmbiguousCaptainTeamError):
        await service.join_by_code(session, code=_CODE, user_id=captain)


async def test_team_mode_explicit_team_must_be_captained_by_caller() -> None:
    guild_id, captain, intruder = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )
    team = _team(guild_id=guild_id, captain_user_id=captain)
    session.store[Team].append(team)

    with pytest.raises(service.NotTeamCaptainError):
        await service.join_by_code(session, code=_CODE, user_id=intruder, team_id=team.id)


async def test_team_mode_is_idempotent_on_re_redeem() -> None:
    guild_id, captain = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )
    team = _team(guild_id=guild_id, captain_user_id=captain)
    session.store[Team].append(team)

    _, first = await service.join_by_code(session, code=_CODE, user_id=captain)
    _, second = await service.join_by_code(session, code=_CODE, user_id=captain)

    assert first.id == second.id
    assert len(session.store[Registration]) == 1
