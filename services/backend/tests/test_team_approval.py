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
from lol_schemas.enums import TeamMemberRole, TeamMemberStatus

from app.models import Team, TeamMember
from app.teams import service
from tests._fake_session import FakeSession

_GUILD = uuid.uuid4()
_CODE = "TEAMKD23"  # 8 chars, all in the non-ambiguous join-code alphabet


def _session_with_team(*, captain: uuid.UUID) -> tuple[FakeSession, Team]:
    session = FakeSession()
    session.current_guild = str(_GUILD)
    team = Team(id=uuid.uuid4(), guild_id=_GUILD, name="SKT T1", captain_user_id=captain,
                join_code=_CODE)
    session.store[Team].append(team)
    session.store[TeamMember].append(
        TeamMember(team_id=team.id, user_id=captain, guild_id=_GUILD,
                   role=TeamMemberRole.CAPTAIN, status=TeamMemberStatus.ACTIVE)
    )
    return session, team


def _fill_active(session: FakeSession, team: Team, n: int) -> None:
    """Add ``n`` extra ACTIVE members to a team (beyond the captain)."""
    for _ in range(n):
        session.store[TeamMember].append(
            TeamMember(team_id=team.id, user_id=uuid.uuid4(), guild_id=_GUILD,
                       role=TeamMemberRole.PLAYER, status=TeamMemberStatus.ACTIVE)
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
        TeamMember(team_id=team.id, user_id=joiner, guild_id=_GUILD,
                   role=TeamMemberRole.PLAYER, status=TeamMemberStatus.PENDING)
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
