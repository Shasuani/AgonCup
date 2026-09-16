"""Join codes and ``/mine`` reads across every guild the caller belongs to.

The desktop client has no server picker, so a tenant request lands on the JWT's
default guild — the one where the user holds their most privileged role. A player
who runs their own server (``guild_admin`` there) and plays in a friend's cup
(``player`` there) therefore redeemed a valid join code against the WRONG tenant
and got "invalid join code". :mod:`app.auth.tenancy` walks the user's own guilds
instead (PLAN.md §3.1 client-first).

Offline, over an RLS-*simulating* fake: guild-scoped rows are visible only while
the acting GUC names their guild, and a user's own ``user_guild_roles`` rows are
also visible once ``app.current_user`` is set (the ``self_role_read`` policy). The
real policies are exercised in ``test_integration_pg.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lol_schemas.enums import GameMode, TeamMemberStatus, TournamentStatus, UserGuildRoleName
from sqlalchemy import select

from app.auth import tenancy
from app.models import Team, TeamMember, Tournament, TournamentParticipant, UserGuildRole
from app.teams import service as teams
from app.tournaments import service as tournaments
from tests._fake_session import FakeSession, _Result

_A, _B, _C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
_USER = uuid.uuid4()
_CODE = "ABCD2345"


class _RlsSession(FakeSession):
    """The shared fake, plus the two policies this feature leans on."""

    def __init__(self) -> None:
        super().__init__()
        self.current_user: str | None = None
        #: Every value the acting GUC was set to, in order (the tenant walk).
        self.visited: list[str | None] = []

    def _execute_text(self, statement: Any, params: Any) -> Any:
        sql = str(statement).lower()
        if "set_config" in sql and params and "uid" in params:
            self.current_user = str(params["uid"])
            return _Result([])
        result = super()._execute_text(statement, params)
        if "set_config" in sql and params and "gid" in params:
            self.visited.append(self.current_guild)
        return result

    def _execute_select(self, statement: Any) -> Any:
        # ``guild_isolation``: a guild-scoped row exists only for its own tenant.
        # ``self_role_read``: a user reads their OWN role rows across guilds.
        full = self.store
        self.store = {
            model: [row for row in rows if self._visible(model, row)]
            for model, rows in full.items()
        }
        try:
            return super()._execute_select(statement)
        finally:
            self.store = full

    def _visible(self, model: type, row: Any) -> bool:
        guild_id = getattr(row, "guild_id", None)
        if guild_id is None:  # global tables (users, riot_accounts)
            return True
        if self.current_guild is not None and str(guild_id) == self.current_guild:
            return True
        return (
            model is UserGuildRole
            and self.current_user is not None
            and str(row.user_id) == self.current_user
        )


def _role(
    guild_id: uuid.UUID, role: UserGuildRoleName, user_id: uuid.UUID = _USER
) -> UserGuildRole:
    return UserGuildRole(id=uuid.uuid4(), user_id=user_id, guild_id=guild_id, role=role)


def _tournament(guild_id: uuid.UUID, *, code: str = _CODE) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Cup",
        game_mode=GameMode.ARAM_1V1,
        status=TournamentStatus.OPEN,
        max_teams=16,
        join_code=code,
    )


def _session(*rows: object, acting: uuid.UUID | None = _A) -> _RlsSession:
    s = _RlsSession()
    for row in rows:
        s.store[type(row)].append(row)
    s.current_guild = str(acting) if acting else None
    return s


# ---------------------------------------------------------------------------
# The helpers themselves
# ---------------------------------------------------------------------------
async def test_guild_ids_for_user_lead_with_the_acting_guild_and_dedupe() -> None:
    s = _session(
        _role(_B, UserGuildRoleName.PLAYER),
        _role(_A, UserGuildRoleName.GUILD_ADMIN),
        _role(_A, UserGuildRoleName.PLAYER),
        _role(_C, UserGuildRoleName.PLAYER, user_id=uuid.uuid4()),  # somebody else's
    )

    assert await tenancy.guild_ids_for_user(s, user_id=_USER) == [str(_A), str(_B)]


async def test_across_user_guilds_unions_each_guilds_rows_and_restores_the_tenant() -> None:
    t_a, t_b, t_c = (
        _tournament(_A),
        _tournament(_B, code="BBBB2345"),
        _tournament(_C, code="CCCC2345"),
    )
    s = _session(
        _role(_A, UserGuildRoleName.GUILD_ADMIN),
        _role(_B, UserGuildRoleName.PLAYER),
        t_a,
        t_b,
        t_c,
    )

    async def _read() -> list[Tournament]:
        return list((await s.execute(select(Tournament))).scalars().all())

    rows = await tenancy.across_user_guilds(s, user_id=_USER, read=_read)

    # A's rows first (the acting guild), then B's; C is not the user's, never read.
    assert [t.id for t in rows] == [t_a.id, t_b.id]
    assert s.visited == [str(_A), str(_B), str(_A)]
    assert s.current_guild == str(_A)


async def test_across_user_guilds_with_no_guild_at_all_reads_once_as_is() -> None:
    s = _session(_tournament(_A), acting=None)
    calls = 0

    async def _read() -> list[Tournament]:
        nonlocal calls
        calls += 1
        return []

    assert await tenancy.across_user_guilds(s, user_id=_USER, read=_read) == []
    assert calls == 1
    assert s.visited == []


# ---------------------------------------------------------------------------
# Tournament join codes
# ---------------------------------------------------------------------------
async def test_a_code_from_another_of_the_callers_guilds_resolves() -> None:
    """The bug: admin of A, player on B, cup on B -> "invalid join code"."""
    t_b = _tournament(_B)
    s = _session(_role(_A, UserGuildRoleName.GUILD_ADMIN), _role(_B, UserGuildRoleName.PLAYER), t_b)

    found = await tournaments.get_tournament_by_code_for_user(s, code="abcd-2345", user_id=_USER)

    assert found is t_b
    # Left on the tournament's guild: the enrolment that follows lands there.
    assert s.current_guild == str(_B)


async def test_join_by_code_enrols_in_the_tournaments_guild_not_the_callers_default() -> None:
    t_b = _tournament(_B)
    s = _session(_role(_A, UserGuildRoleName.GUILD_ADMIN), _role(_B, UserGuildRoleName.PLAYER), t_b)

    tournament, enrolment = await tournaments.join_by_code(s, code=_CODE, user_id=_USER)

    assert tournament is t_b
    assert isinstance(enrolment, TournamentParticipant)
    assert enrolment.guild_id == _B
    # The 1v1 scaffolding team was written into B as well — never into A.
    assert {t.guild_id for t in s.store[Team]} == {_B}


async def test_a_code_from_a_guild_the_caller_is_not_in_stays_invalid() -> None:
    """No widening: a leaked code still cannot cross into a server you are not on."""
    s = _session(_role(_A, UserGuildRoleName.GUILD_ADMIN), _tournament(_C))

    with pytest.raises(tournaments.InvalidJoinCodeError):
        await tournaments.get_tournament_by_code_for_user(s, code=_CODE, user_id=_USER)

    # The miss put the transaction back on the tenant it started on.
    assert s.current_guild == str(_A)
    assert s.visited[-1] == str(_A)


async def test_the_acting_guild_is_tried_first() -> None:
    t_a = _tournament(_A)
    s = _session(_role(_A, UserGuildRoleName.PLAYER), _role(_B, UserGuildRoleName.PLAYER), t_a)

    assert await tournaments.get_tournament_by_code_for_user(s, code=_CODE, user_id=_USER) is t_a
    assert s.visited == [str(_A)]


# ---------------------------------------------------------------------------
# Team join codes — same cure
# ---------------------------------------------------------------------------
async def test_a_team_code_from_another_of_the_callers_guilds_resolves() -> None:
    captain = uuid.uuid4()
    team_b = Team(
        id=uuid.uuid4(), guild_id=_B, name="Alpha", captain_user_id=captain, join_code=_CODE
    )
    s = _session(
        _role(_A, UserGuildRoleName.GUILD_ADMIN),
        _role(_B, UserGuildRoleName.PLAYER),
        team_b,
        TeamMember(
            team_id=team_b.id,
            user_id=captain,
            guild_id=_B,
            status=TeamMemberStatus.ACTIVE,
        ),
    )

    team, member = await teams.join_by_code(s, code=_CODE, user_id=_USER)

    assert team is team_b
    assert member.status is TeamMemberStatus.PENDING
    assert member.guild_id == _B
