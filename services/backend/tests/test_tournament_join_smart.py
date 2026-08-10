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
from lol_schemas.enums import (
    GameMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentStatus,
)

from app.models import Match, Registration, Team, TeamMember, Tournament, TournamentParticipant
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


async def test_solo_mode_also_registers_a_hidden_one_player_team() -> None:
    """The bracket seeds from registrations, so a solo entrant needs one.

    The player creates no team: the join mints an ``is_solo`` team captained by
    them (so the bracket shows their name) and registers it PENDING — they still
    have to lock in.
    """
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _seed(session, _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1))
    session.current_guild = str(guild_id)

    _, participant = await service.join_by_code(session, code=_CODE, user_id=user_id)

    solo_teams = session.store[Team]
    assert len(solo_teams) == 1
    assert solo_teams[0].is_solo is True
    assert solo_teams[0].captain_user_id == user_id
    assert solo_teams[0].join_code is None  # nobody can be invited into it

    regs = session.store[Registration]
    assert len(regs) == 1
    assert regs[0].team_id == solo_teams[0].id
    assert regs[0].status is RegistrationStatus.PENDING
    assert participant.team_id == solo_teams[0].id


async def test_solo_join_is_idempotent_and_reuses_the_same_solo_team() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _seed(session, _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1))
    session.current_guild = str(guild_id)

    await service.join_by_code(session, code=_CODE, user_id=user_id)
    await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert len(session.store[TournamentParticipant]) == 1
    assert len(session.store[Team]) == 1
    assert len(session.store[Registration]) == 1


async def test_solo_team_does_not_make_a_later_5v5_join_ambiguous() -> None:
    """The 1v1 scaffolding must stay invisible to the team path.

    A solo team is captained by its player, so counting it would make anyone who
    had ever entered a 1v1 fail a later 5v5 join with AmbiguousCaptainTeamError.
    """
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    real_team = _team(guild_id=guild_id, captain_user_id=user_id)
    session.store[Team].append(real_team)
    session.store[Team].append(
        Team(
            id=uuid.uuid4(),
            guild_id=guild_id,
            name="Solo",
            captain_user_id=user_id,
            is_solo=True,
        )
    )
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    )

    _, reg = await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert isinstance(reg, Registration)
    assert reg.team_id == real_team.id


async def test_solo_player_locks_in_without_naming_a_team() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    _seed(session, tournament)
    session.current_guild = str(guild_id)

    await service.join_by_code(session, code=_CODE, user_id=user_id)
    team_id = await service.resolve_check_in_team(
        session, tournament_id=tournament.id, user_id=user_id
    )
    reg = await service.check_in(session, tournament_id=tournament.id, team_id=team_id)

    assert team_id == session.store[Team][0].id
    assert reg.status is RegistrationStatus.CHECKED_IN


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


async def test_solo_1v1_runs_end_to_end_from_join_to_a_started_bracket() -> None:
    """The whole 1v1 path, which previously dead-ended at ``/tournament-start``.

    Two players redeem the code, each locks themselves in, and the admin starts:
    the bracket seeds from their solo teams, so the match carries their names
    rather than an empty slot.
    """
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    _seed(session, tournament)
    session.current_guild = str(guild_id)

    for player in (alice, bob):
        await service.join_by_code(session, code=_CODE, user_id=player)
        team_id = await service.resolve_check_in_team(
            session, tournament_id=tournament.id, user_id=player
        )
        await service.check_in(session, tournament_id=tournament.id, team_id=team_id)

    result = await service.start_tournament(session, tournament_id=tournament.id)

    assert result.matches_created == 1
    assert result.byes == 0
    assert tournament.status is TournamentStatus.RUNNING
    seeded = {m.team_a for m in session.store[Match]} | {m.team_b for m in session.store[Match]}
    assert seeded == {t.id for t in session.store[Team]}


async def test_entrants_flags_the_callers_own_entry_and_lock_in_state() -> None:
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    _seed(session, tournament)
    session.current_guild = str(guild_id)

    await service.join_by_code(session, code=_CODE, user_id=alice)
    await service.join_by_code(session, code=_CODE, user_id=bob)
    alice_team = await service.resolve_check_in_team(
        session, tournament_id=tournament.id, user_id=alice
    )
    await service.check_in(session, tournament_id=tournament.id, team_id=alice_team)

    entrants = await service.list_entrants(session, tournament_id=tournament.id, user_id=alice)

    assert len(entrants) == 2
    mine = [e for e in entrants if e.is_you]
    assert len(mine) == 1
    assert mine[0].team_id == alice_team
    assert mine[0].locked_in is True
    assert mine[0].is_solo is True
    theirs = [e for e in entrants if not e.is_you]
    assert theirs[0].locked_in is False


async def _member(*, team: Team, user_id: uuid.UUID) -> TeamMember:
    return TeamMember(
        team_id=team.id,
        user_id=user_id,
        guild_id=team.guild_id,
        role=TeamMemberRole.CAPTAIN,
        status=TeamMemberStatus.ACTIVE,
    )


async def test_5v5_lock_in_picks_the_captained_team_entered_in_THIS_tournament() -> None:
    """A captain of several teams must still be able to lock in with no team_id.

    Only one of their teams is in this tournament, so the answer is unambiguous —
    resolving against "teams you captain" alone would wrongly reject them.
    """
    guild_id, captain = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    entered = _team(guild_id=guild_id, captain_user_id=captain, name="Alpha")
    other = _team(guild_id=guild_id, captain_user_id=captain, name="Beta")
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    session.store[Tournament].append(tournament)
    session.store[Team].extend([entered, other])

    await service.join_by_code(session, code=_CODE, user_id=captain, team_id=entered.id)
    team_id = await service.resolve_check_in_team(
        session, tournament_id=tournament.id, user_id=captain
    )
    reg = await service.check_in(session, tournament_id=tournament.id, team_id=team_id)

    assert team_id == entered.id
    assert reg.status is RegistrationStatus.CHECKED_IN


async def test_5v5_lock_in_is_rejected_for_someone_with_no_entry() -> None:
    guild_id, outsider = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    session.store[Tournament].append(tournament)

    with pytest.raises(service.NoCaptainTeamError):
        await service.resolve_check_in_team(session, tournament_id=tournament.id, user_id=outsider)


async def test_entrants_gives_every_roster_member_their_own_lock_in() -> None:
    """The captain enrols the team; each player still locks themselves in.

    Nobody but the captain ever touches the join code, so a plain member must find
    themselves already entered — and holding a live Lock in button, not a read-only
    view of their captain's decision.
    """
    guild_id, captain, player = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    team = _team(guild_id=guild_id, captain_user_id=captain)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    session.store[Tournament].append(tournament)
    session.store[Team].append(team)
    session.store[TeamMember].append(await _member(team=team, user_id=captain))
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=player,
            guild_id=guild_id,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )

    await service.join_by_code(session, code=_CODE, user_id=captain)

    as_captain = await service.list_entrants(session, tournament_id=tournament.id, user_id=captain)
    as_player = await service.list_entrants(session, tournament_id=tournament.id, user_id=player)

    assert as_captain[0].is_you is True
    assert as_captain[0].can_lock_in is True
    assert as_captain[0].is_solo is False
    # The teammate never redeemed the code, yet is in and can lock in.
    assert as_player[0].is_you is True
    assert as_player[0].can_lock_in is True
    assert {p.user_id for p in as_player[0].players} == {captain, player}
    assert all(p.locked_in is False for p in as_player[0].players)


async def test_5v5_team_is_only_checked_in_once_every_member_locked_in() -> None:
    """One captain pressing Lock in must not seed a bracket for five people.

    The captain's word is not evidence that the rest of the roster is at their
    keyboards — so the registration stays PENDING (unseedable) until the last
    member locks in, and only then flips to CHECKED_IN.
    """
    guild_id, captain, player = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    team = _team(guild_id=guild_id, captain_user_id=captain)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    session.store[Tournament].append(tournament)
    session.store[Team].append(team)
    session.store[TeamMember].append(await _member(team=team, user_id=captain))
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=player,
            guild_id=guild_id,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )

    await service.join_by_code(session, code=_CODE, user_id=captain)

    reg = await service.check_in_self(session, tournament_id=tournament.id, user_id=captain)
    assert reg.status is RegistrationStatus.PENDING, "one of two locked in is not a ready team"

    entrants = await service.list_entrants(session, tournament_id=tournament.id, user_id=captain)
    assert entrants[0].can_lock_in is False, "already locked in — no second button"
    assert {p.user_id for p in entrants[0].players if p.locked_in} == {captain}

    reg = await service.check_in_self(session, tournament_id=tournament.id, user_id=player)
    assert reg.status is RegistrationStatus.CHECKED_IN
