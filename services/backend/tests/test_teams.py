"""Tests for the pure team membership-validation logic (PLAN.md §2 data model).

``validate_add_member`` / ``validate_remove_member`` are pure (no DB), so the
membership-transition rules — assignable roles, duplicate guard, the captain
protection — are tested exhaustively here. The async CRUD in the service is
RLS-dependent and covered by the Postgres integration suite once a DB is wired in.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lol_schemas.enums import TeamMemberRole

from app.teams.service import (
    ADDABLE_ROLES,
    AmbiguousTeamError,
    CaptainProtectedError,
    DuplicateMemberError,
    InvalidRoleError,
    MemberNotFoundError,
    MembershipChange,
    TeamNotFoundError,
    get_team_by_name,
    validate_add_member,
    validate_remove_member,
)


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _NameSession:
    """Returns a canned team list for the get_team_by_name lookup (RLS is real DB)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return _ScalarResult(self._rows)


# ---------------------------------------------------------------------------
# get_team_by_name (name resolution for /team join)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_team_by_name_returns_single_match() -> None:
    from app.models import Team

    team = Team(id=uuid.uuid4(), guild_id=uuid.uuid4(), name="Alpha", captain_user_id=uuid.uuid4())
    result = await get_team_by_name(_NameSession([team]), name="  alpha ")  # type: ignore[arg-type]
    assert result is team


@pytest.mark.asyncio
async def test_get_team_by_name_missing_raises_not_found() -> None:
    with pytest.raises(TeamNotFoundError):
        await get_team_by_name(_NameSession([]), name="Ghost")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_team_by_name_collision_raises_ambiguous() -> None:
    from app.models import Team

    gid = uuid.uuid4()
    dupes = [
        Team(id=uuid.uuid4(), guild_id=gid, name="Alpha", captain_user_id=uuid.uuid4())
        for _ in range(2)
    ]
    with pytest.raises(AmbiguousTeamError):
        await get_team_by_name(_NameSession(dupes), name="Alpha")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_add_member
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", [TeamMemberRole.PLAYER, TeamMemberRole.SUB])
def test_add_player_or_sub_to_empty_team(role: TeamMemberRole) -> None:
    uid = uuid.uuid4()
    change = validate_add_member(user_id=uid, role=role, existing_member_user_ids=frozenset())
    assert change == MembershipChange(user_id=uid, role=role)


def test_add_captain_role_is_rejected() -> None:
    # Captain is created with the team, never granted via add_member.
    with pytest.raises(InvalidRoleError):
        validate_add_member(
            user_id=uuid.uuid4(),
            role=TeamMemberRole.CAPTAIN,
            existing_member_user_ids=frozenset(),
        )


def test_addable_roles_are_exactly_player_and_sub() -> None:
    assert frozenset({TeamMemberRole.PLAYER, TeamMemberRole.SUB}) == ADDABLE_ROLES
    assert TeamMemberRole.CAPTAIN not in ADDABLE_ROLES


def test_add_existing_member_is_duplicate() -> None:
    uid = uuid.uuid4()
    with pytest.raises(DuplicateMemberError):
        validate_add_member(
            user_id=uid,
            role=TeamMemberRole.PLAYER,
            existing_member_user_ids=frozenset({uid}),
        )


def test_add_distinct_member_when_team_has_others() -> None:
    existing = frozenset(_ids(4))
    uid = uuid.uuid4()
    change = validate_add_member(
        user_id=uid, role=TeamMemberRole.SUB, existing_member_user_ids=existing
    )
    assert change.user_id == uid and change.role == TeamMemberRole.SUB


# ---------------------------------------------------------------------------
# validate_remove_member
# ---------------------------------------------------------------------------
def test_remove_non_member_raises() -> None:
    with pytest.raises(MemberNotFoundError):
        validate_remove_member(
            user_id=uuid.uuid4(),
            captain_user_id=uuid.uuid4(),
            member_role=None,
        )


def test_remove_captain_is_protected() -> None:
    cap = uuid.uuid4()
    with pytest.raises(CaptainProtectedError):
        validate_remove_member(user_id=cap, captain_user_id=cap, member_role=TeamMemberRole.CAPTAIN)


def test_remove_captain_protected_even_with_forged_non_captain_role() -> None:
    # The guard keys on the team's captain_user_id, not the stored member role, so
    # a stale/forged role cannot smuggle the captain out.
    cap = uuid.uuid4()
    with pytest.raises(CaptainProtectedError):
        validate_remove_member(user_id=cap, captain_user_id=cap, member_role=TeamMemberRole.PLAYER)


@pytest.mark.parametrize("role", [TeamMemberRole.PLAYER, TeamMemberRole.SUB])
def test_remove_regular_member_succeeds(role: TeamMemberRole) -> None:
    uid, cap = uuid.uuid4(), uuid.uuid4()
    change = validate_remove_member(user_id=uid, captain_user_id=cap, member_role=role)
    assert change == MembershipChange(user_id=uid, role=role)
