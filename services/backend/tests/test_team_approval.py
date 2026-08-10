"""Team-leader approval flow — pending → active membership (PLAN.md §3.1).

Offline branch/guard tests over the shared fake session: redeeming a code creates
a PENDING request (not immediate membership), the active roster excludes pending
rows, and approve/reject transition or remove the pending row. The joiner-named
listing queries (``list_join_requests`` / ``list_my_join_requests``) use SQL JOINs
the fake cannot model and are exercised live against Postgres instead.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import (
    GameMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentFormat,
    TournamentStatus,
)

from app.models import Registration, Team, TeamMember, Tournament
from app.teams import service
from tests._fake_session import FakeSession

_GUILD = uuid.uuid4()
_CODE = "TEAMKD23"  # 8 chars, all in the non-ambiguous join-code alphabet


def _session_with_team(*, captain: uuid.UUID) -> tuple[FakeSession, Team]:
    session = FakeSession()
    session.current_guild = str(_GUILD)
    team = Team(
        id=uuid.uuid4(), guild_id=_GUILD, name="SKT T1", captain_user_id=captain, join_code=_CODE
    )
    session.store[Team].append(team)
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=captain,
            guild_id=_GUILD,
            role=TeamMemberRole.CAPTAIN,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    return session, team


def _fill_active(session: FakeSession, team: Team, n: int) -> None:
    """Add ``n`` extra ACTIVE members to a team (beyond the captain)."""
    for _ in range(n):
        session.store[TeamMember].append(
            TeamMember(
                team_id=team.id,
                user_id=uuid.uuid4(),
                guild_id=_GUILD,
                role=TeamMemberRole.PLAYER,
                status=TeamMemberStatus.ACTIVE,
            )
        )


async def test_cannot_request_join_when_team_full() -> None:
    session, team = _session_with_team(captain=uuid.uuid4())
    _fill_active(session, team, 4)  # captain + 4 = 5 = MAX_TEAM_SIZE

    with pytest.raises(service.TeamFullError):
        await service.join_by_code(session, code=_CODE, user_id=uuid.uuid4())


async def test_cannot_approve_beyond_max_team_size() -> None:
    session, team = _session_with_team(captain=uuid.uuid4())
    _fill_active(session, team, 4)  # roster already at the cap (5)
    joiner = uuid.uuid4()
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=joiner,
            guild_id=_GUILD,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.PENDING,
        )
    )
    with pytest.raises(service.TeamFullError):
        await service.approve_join_request(session, team_id=team.id, user_id=joiner)


async def test_join_by_code_creates_a_pending_request() -> None:
    captain, joiner = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)

    _, member = await service.join_by_code(session, code=_CODE, user_id=joiner)

    assert member.status == TeamMemberStatus.PENDING
    assert member.role == TeamMemberRole.PLAYER
    assert member.user_id == joiner


async def test_pending_request_is_not_in_the_active_roster() -> None:
    captain, joiner = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    await service.join_by_code(session, code=_CODE, user_id=joiner)

    roster = await service.list_members(session, team_id=team.id)

    ids = {m.user_id for m in roster}
    assert captain in ids  # the captain is active
    assert joiner not in ids  # the pending joiner is NOT a member yet


async def test_approve_activates_the_member() -> None:
    captain, joiner = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    await service.join_by_code(session, code=_CODE, user_id=joiner)

    member = await service.approve_join_request(session, team_id=team.id, user_id=joiner)

    assert member.status == TeamMemberStatus.ACTIVE
    roster = await service.list_members(session, team_id=team.id)
    assert joiner in {m.user_id for m in roster}


async def test_reject_removes_the_pending_row() -> None:
    captain, joiner = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    await service.join_by_code(session, code=_CODE, user_id=joiner)

    await service.reject_join_request(session, team_id=team.id, user_id=joiner)

    remaining = [m for m in session.store[TeamMember] if m.user_id == joiner]
    assert remaining == []


async def test_approve_without_a_pending_request_raises() -> None:
    captain, stranger = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)

    with pytest.raises(service.MemberNotFoundError):
        await service.approve_join_request(session, team_id=team.id, user_id=stranger)


async def test_join_is_idempotent_no_duplicate_request() -> None:
    captain, joiner = uuid.uuid4(), uuid.uuid4()
    session, team = _session_with_team(captain=captain)

    await service.join_by_code(session, code=_CODE, user_id=joiner)
    await service.join_by_code(session, code=_CODE, user_id=joiner)

    joiner_rows = [m for m in session.store[TeamMember] if m.user_id == joiner]
    assert len(joiner_rows) == 1


# --- transfer_captain (edge-cases §1.4) ------------------------------------
def _role_of(session: FakeSession, team_id: uuid.UUID, user_id: uuid.UUID) -> TeamMemberRole:
    return next(
        m.role for m in session.store[TeamMember] if m.team_id == team_id and m.user_id == user_id
    )


async def test_transfer_captain_swaps_roles_and_repoints_team() -> None:
    captain = uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    heir = uuid.uuid4()
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=heir,
            guild_id=_GUILD,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )

    result = await service.transfer_captain(session, team_id=team.id, new_captain_user_id=heir)

    assert result.captain_user_id == heir
    assert _role_of(session, team.id, heir) == TeamMemberRole.CAPTAIN
    assert _role_of(session, team.id, captain) == TeamMemberRole.PLAYER


async def test_transfer_lets_old_captain_leave() -> None:
    captain = uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    heir = uuid.uuid4()
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=heir,
            guild_id=_GUILD,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    # Before transfer the captain is protected from leaving.
    with pytest.raises(service.CaptainProtectedError):
        await service.leave_team(session, team_id=team.id, user_id=captain)

    await service.transfer_captain(session, team_id=team.id, new_captain_user_id=heir)
    await service.leave_team(session, team_id=team.id, user_id=captain)  # now allowed

    assert not [m for m in session.store[TeamMember] if m.user_id == captain]


async def test_transfer_to_non_member_rejected() -> None:
    session, team = _session_with_team(captain=uuid.uuid4())
    with pytest.raises(service.NewCaptainNotEligibleError):
        await service.transfer_captain(session, team_id=team.id, new_captain_user_id=uuid.uuid4())


async def test_transfer_to_pending_member_rejected() -> None:
    session, team = _session_with_team(captain=uuid.uuid4())
    pending = uuid.uuid4()
    session.store[TeamMember].append(
        TeamMember(
            team_id=team.id,
            user_id=pending,
            guild_id=_GUILD,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.PENDING,
        )
    )
    with pytest.raises(service.NewCaptainNotEligibleError):
        await service.transfer_captain(session, team_id=team.id, new_captain_user_id=pending)


async def test_transfer_to_current_captain_is_noop() -> None:
    captain = uuid.uuid4()
    session, team = _session_with_team(captain=captain)
    result = await service.transfer_captain(session, team_id=team.id, new_captain_user_id=captain)
    assert result.captain_user_id == captain
    assert _role_of(session, team.id, captain) == TeamMemberRole.CAPTAIN


async def test_growing_the_roster_un_readies_an_unstarted_registration() -> None:
    """A locked-in team that takes another player is not locked in any more.

    Lock-in means "every one of us is here". Letting the flag survive a roster
    change would seed a bracket with a player who never confirmed — the exact
    thing per-player lock-in exists to prevent. The newcomer pressing Lock in is
    what flips it back.
    """
    session, team = _session_with_team(captain=uuid.uuid4())
    tournament = Tournament(
        id=uuid.uuid4(),
        guild_id=_GUILD,
        name="Open Cup",
        status=TournamentStatus.OPEN,
        format=TournamentFormat.SINGLE_ELIMINATION,
        game_mode=GameMode.SUMMONERS_RIFT_5V5,
        best_of=1,
        max_teams=8,
    )
    reg = Registration(
        id=uuid.uuid4(),
        guild_id=_GUILD,
        tournament_id=tournament.id,
        team_id=team.id,
        status=RegistrationStatus.CHECKED_IN,
    )
    session.store[Tournament].append(tournament)
    session.store[Registration].append(reg)

    await service.add_member(session, team_id=team.id, user_id=uuid.uuid4())

    assert reg.status is RegistrationStatus.PENDING


async def test_a_running_tournaments_registration_is_left_alone() -> None:
    """History is not rewritten under a bracket that already started."""
    session, team = _session_with_team(captain=uuid.uuid4())
    tournament = Tournament(
        id=uuid.uuid4(),
        guild_id=_GUILD,
        name="Live Cup",
        status=TournamentStatus.RUNNING,
        format=TournamentFormat.SINGLE_ELIMINATION,
        game_mode=GameMode.SUMMONERS_RIFT_5V5,
        best_of=1,
        max_teams=8,
    )
    reg = Registration(
        id=uuid.uuid4(),
        guild_id=_GUILD,
        tournament_id=tournament.id,
        team_id=team.id,
        status=RegistrationStatus.CHECKED_IN,
    )
    session.store[Tournament].append(tournament)
    session.store[Registration].append(reg)

    await service.add_member(session, team_id=team.id, user_id=uuid.uuid4())

    assert reg.status is RegistrationStatus.CHECKED_IN
