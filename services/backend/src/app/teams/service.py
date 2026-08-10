"""Team domain service: CRUD + membership management (PLAN.md §2 data model).

Guild scoping is enforced by RLS on the tenant session (the ``app.current_guild``
GUC is already set by ``get_tenant_session``); inserts read the acting guild back
from the GUC via :func:`_current_guild` so a row can never be written for the wrong
tenant (the ``guild_isolation`` WITH CHECK would reject it anyway — this just
supplies the value).

The same-guild invariant between a team and its members is **structural**: a
:class:`~app.models.TeamMember` carries a composite FK
``(team_id, guild_id) -> teams(id, guild_id)`` (PLAN.md §2), so a member can only
ever reference a same-guild team. Every membership row written here takes its
``guild_id`` from the same resolved GUC the parent team was created under, so the
composite FK is satisfied by construction rather than by a runtime guild compare.

The membership-transition rules (what role a new member gets, the duplicate guard,
the captain-leave guard) are pure and live in :func:`validate_add_member` /
:func:`validate_remove_member` so they are exhaustively unit-testable with no DB.
The async CRUD mirrors ``app.tournaments.service`` (pure-where-possible +
service/router split).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from lol_schemas.enums import (
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentStatus,
)
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.joincodes import generate_join_code, normalize_join_code
from app.logging import get_logger
from app.models import Registration, Team, TeamMember, Tournament, User

logger = get_logger(__name__)

# Roles a member may be *added* with (the captain is created implicitly, never
# re-assigned via add_member).
ADDABLE_ROLES: frozenset[TeamMemberRole] = frozenset({TeamMemberRole.PLAYER, TeamMemberRole.SUB})


class TeamError(Exception):
    """Base class for team service failures."""


class TeamNotFoundError(TeamError):
    """No team with that id in the acting guild."""


class MemberNotFoundError(TeamError):
    """The user is not a member of the team."""


class DuplicateMemberError(TeamError):
    """The user is already a member of the team."""


class InvalidRoleError(TeamError):
    """The requested role is not assignable via add_member (e.g. ``captain``)."""


class CaptainProtectedError(TeamError):
    """The captain cannot be removed/leave while still captain (transfer first)."""


class AmbiguousTeamError(TeamError):
    """More than one team in the acting guild shares that name — use the id."""


class InvalidJoinCodeError(TeamError):
    """No team in the acting guild has that join code (bad/foreign code)."""


class TeamFullError(TeamError):
    """The team is already at its ACTIVE-member cap (``MAX_TEAM_SIZE``)."""


class NewCaptainNotEligibleError(TeamError):
    """Captaincy can only be transferred to an existing ACTIVE member of the team."""


#: Max ACTIVE members per team — a 5v5 roster (the captain counts as one of the 5).
MAX_TEAM_SIZE = 5


async def _current_guild(session: AsyncSession) -> uuid.UUID:
    """Read the acting guild from the transaction GUC (set by get_tenant_session)."""
    gid = (
        await session.execute(text("SELECT current_setting('app.current_guild', true)"))
    ).scalar_one_or_none()
    if not gid:
        # Fail closed: never write a guild-scoped row without a resolved tenant.
        raise TeamError("no acting guild on the session")
    return uuid.UUID(gid)


# ---------------------------------------------------------------------------
# Pure membership-change validation (no DB — exhaustively unit-tested)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MembershipChange:
    """The resolved, validated outcome of a membership mutation.

    ``role`` is the role to write (only meaningful for an add). The dataclass is a
    pure return value so the decision can be asserted on without a DB.
    """

    user_id: uuid.UUID
    role: TeamMemberRole


def validate_add_member(
    *,
    user_id: uuid.UUID,
    role: TeamMemberRole,
    existing_member_user_ids: frozenset[uuid.UUID],
) -> MembershipChange:
    """Decide whether ``user_id`` may be added to a team with ``role`` (pure).

    Rules (PLAN.md §2 ``TeamMember.role``):

    * Only ``player``/``sub`` are assignable here — ``captain`` is created when the
      team is created and is never granted via add_member (raise
      :class:`InvalidRoleError`).
    * A user already on the team cannot be added twice (raise
      :class:`DuplicateMemberError`); the composite PK ``(team_id, user_id)`` would
      reject it anyway — this just turns the race into a clean domain error.
    """
    if role not in ADDABLE_ROLES:
        raise InvalidRoleError(f"role {role.value!r} is not assignable via add_member")
    if user_id in existing_member_user_ids:
        raise DuplicateMemberError(str(user_id))
    return MembershipChange(user_id=user_id, role=role)


def validate_remove_member(
    *,
    user_id: uuid.UUID,
    captain_user_id: uuid.UUID,
    member_role: TeamMemberRole | None,
) -> MembershipChange:
    """Decide whether ``user_id`` may be removed/leave the team (pure).

    ``member_role`` is ``None`` when the user is not on the team (raise
    :class:`MemberNotFoundError`). The captain is protected: removing the captain
    while they are still captain would orphan the team's ``captain_user_id`` (a
    RESTRICT FK to ``users``), so it is rejected with :class:`CaptainProtectedError`
    — captaincy must be transferred first. Note ``captain_user_id`` is the team's
    captain, independent of the member's stored role, so a stale/forged ``role``
    cannot smuggle the captain out.
    """
    if member_role is None:
        raise MemberNotFoundError(str(user_id))
    if user_id == captain_user_id:
        raise CaptainProtectedError("the captain cannot leave/be removed while captain")
    return MembershipChange(user_id=user_id, role=member_role)


# ---------------------------------------------------------------------------
# CRUD + membership (async; tenant-scoped via RLS)
# ---------------------------------------------------------------------------
async def create_team(session: AsyncSession, *, name: str, captain_user_id: uuid.UUID) -> Team:
    """Create a team in the acting guild; the captain becomes a captain TeamMember.

    The team and the captain's membership row are written under the **same**
    resolved guild (read from the GUC), so the composite FK
    ``(team_id, guild_id) -> teams(id, guild_id)`` is satisfied structurally and
    the captain can never be a member of a different-guild team (PLAN.md §2
    same-guild invariant).
    """
    guild_id = await _current_guild(session)
    team = Team(
        guild_id=guild_id,
        name=name,
        captain_user_id=captain_user_id,
        join_code=generate_join_code(),
    )
    session.add(team)
    await session.flush()  # assign team.id before the membership row references it
    session.add(
        TeamMember(
            team_id=team.id,
            user_id=captain_user_id,
            guild_id=guild_id,
            role=TeamMemberRole.CAPTAIN,
            status=TeamMemberStatus.ACTIVE,  # the captain is a member from creation
        )
    )
    await session.flush()
    return team


async def get_team(session: AsyncSession, *, team_id: uuid.UUID) -> Team:
    """Load a team (RLS already scopes to the acting guild)."""
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise TeamNotFoundError(str(team_id))
    return team


async def get_team_by_name(session: AsyncSession, *, name: str) -> Team:
    """Resolve a team by display name within the acting guild (RLS-scoped).

    Case-insensitive. Team names are NOT unique per guild, so this is a
    convenience for the Discord ``/team join <name>`` flow: no match raises
    :class:`TeamNotFoundError`; more than one raises :class:`AmbiguousTeamError`
    (the user should fall back to the exact id).
    """
    rows = (
        (await session.execute(select(Team).where(func.lower(Team.name) == name.strip().lower())))
        .scalars()
        .all()
    )
    if not rows:
        raise TeamNotFoundError(name)
    if len(rows) > 1:
        raise AmbiguousTeamError(name)
    return rows[0]


def _looks_like_id_prefix(ref: str) -> bool:
    """True if ``ref`` is a short hex id prefix (e.g. ``b00cc7da``) — >=4 hex chars."""
    r = ref.strip().lower()
    return len(r) >= 4 and all(c in "0123456789abcdef" for c in r)


async def get_team_by_id_prefix(session: AsyncSession, *, prefix: str) -> Team:
    """Resolve a team by the short id prefix shown to users (RLS-scoped).

    Matches a team whose full UUID starts with ``prefix`` (the first 8 hex chars we
    display). No match -> :class:`TeamNotFoundError`; a prefix shared by more than
    one team -> :class:`AmbiguousTeamError`.
    """
    p = prefix.strip().lower()
    rows = (
        (await session.execute(select(Team).where(cast(Team.id, String).like(p + "%"))))
        .scalars()
        .all()
    )
    if not rows:
        raise TeamNotFoundError(prefix)
    if len(rows) > 1:
        raise AmbiguousTeamError(prefix)
    return rows[0]


async def resolve_team_ref(session: AsyncSession, *, ref: str) -> Team:
    """Resolve a team from a full id, a name, OR a short id prefix (Discord flow).

    Precedence: exact UUID -> exact name -> short hex-id prefix. Ambiguity on a name
    or prefix propagates so the user can disambiguate with the exact id.
    """
    ref = ref.strip()
    try:
        return await get_team(session, team_id=uuid.UUID(ref))
    except ValueError:
        pass
    try:
        return await get_team_by_name(session, name=ref)
    except TeamNotFoundError:
        if _looks_like_id_prefix(ref):
            return await get_team_by_id_prefix(session, prefix=ref)
        raise


async def list_teams(session: AsyncSession) -> list[Team]:
    # `is_solo` teams are the one-player scaffolding behind a 1v1 enrolment, not
    # teams anyone formed — they stay out of every roster listing.
    return list(
        (await session.execute(select(Team).where(Team.is_solo.is_(False)))).scalars().all()
    )


@dataclass(frozen=True, slots=True)
class TeamOverview:
    """One row of the teams listing, with the two facts a list actually needs.

    ``Team`` alone only carries ``captain_user_id`` — a UUID — so every caller
    that wanted to SHOW who leads a team had to fetch the roster per row just to
    resolve one name. The captain's display name and the roster size are joined
    here instead, in a single query (PLAN.md §3.1 teams).
    """

    team: Team
    #: The captain's Discord display name. None only if the user row vanished.
    captain_name: str | None
    #: Active roster size, the captain included.
    member_count: int


async def list_teams_overview(session: AsyncSession) -> list[TeamOverview]:
    """Teams in the acting guild with the captain's name and roster size.

    One query: LEFT JOIN ``users`` for the captain's name (``users`` is a global
    table with no RLS policy, so it joins cleanly under the tenant session), and
    a correlated count over ``team_members`` — which IS guild-scoped, so the
    count is RLS-bound to the acting guild like everything else.
    """
    member_count = (
        select(func.count())
        .select_from(TeamMember)
        .where(
            TeamMember.team_id == Team.id,
            # ACTIVE only — the same definition of "roster" every other reader
            # uses (:func:`list_roster`, :func:`_active_member_count`, and the
            # MAX_TEAM_SIZE cap). Counting PENDING rows too would report 7/5 on
            # a team at its cap with two requests waiting, and disagree with the
            # roster the very next click shows.
            TeamMember.status == TeamMemberStatus.ACTIVE,
        )
        .correlate(Team)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Team, User.display_name, member_count)
            # LEFT: a team whose captain row was purged still has to list.
            .outerjoin(User, User.id == Team.captain_user_id)
            .where(Team.is_solo.is_(False))
            .order_by(Team.name)
        )
    ).all()
    return [
        TeamOverview(team=team, captain_name=captain_name, member_count=count)
        for team, captain_name, count in rows
    ]


async def get_team_by_code(session: AsyncSession, *, code: str) -> Team:
    """Resolve a team by its join code within the acting guild (RLS-scoped).

    Normalizes the code (upper-cased, separators dropped). A code that belongs to
    another guild is invisible under RLS and reads as invalid. No match ->
    :class:`InvalidJoinCodeError`.
    """
    normalized = normalize_join_code(code)
    if not normalized:
        raise InvalidJoinCodeError("empty code")
    team = (
        await session.execute(select(Team).where(Team.join_code == normalized))
    ).scalar_one_or_none()
    if team is None:
        raise InvalidJoinCodeError(normalized)
    return team


async def join_by_code(
    session: AsyncSession, *, code: str, user_id: uuid.UUID
) -> tuple[Team, TeamMember]:
    """Request to join a team by its join code — creates a PENDING membership.

    The redeemer does NOT join immediately: a ``pending`` request is created for
    the captain to approve/reject (PLAN.md §3.1 team-leader approval flow).
    Idempotent — redeeming a code you already hold ANY membership for (pending or
    active) returns the existing row rather than raising :class:`DuplicateMemberError`.
    Captains keep their (active) captain membership.
    """
    team = await get_team_by_code(session, code=code)
    existing_role = await _member_role(session, team_id=team.id, user_id=user_id)
    if existing_role is not None:
        member = (
            await session.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team.id, TeamMember.user_id == user_id
                )
            )
        ).scalar_one()
        return team, member
    # A full roster (MAX_TEAM_SIZE active) can't take more requests.
    if await _active_member_count(session, team.id) >= MAX_TEAM_SIZE:
        raise TeamFullError(str(MAX_TEAM_SIZE))
    member = await add_member(
        session,
        team_id=team.id,
        user_id=user_id,
        role=TeamMemberRole.PLAYER,
        status=TeamMemberStatus.PENDING,
    )
    return team, member


async def _active_member_count(session: AsyncSession, team_id: uuid.UUID) -> int:
    """Count of ACTIVE members on a team (the roster size, cap-checked)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
        )
    ).scalar_one()


async def list_members(session: AsyncSession, *, team_id: uuid.UUID) -> list[TeamMember]:
    """List a team's ACTIVE members (the roster). Verifies the team exists here.

    Pending join requests are excluded — they are not members yet. Use
    :func:`list_join_requests` for the captain's approval queue.
    """
    await get_team(session, team_id=team_id)
    rows = (
        await session.execute(
            select(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
            .order_by(TeamMember.user_id)
        )
    ).scalars()
    return list(rows)


@dataclass(frozen=True)
class MyTeam:
    """A team the caller belongs to, with their role + membership status."""

    team_id: uuid.UUID
    name: str
    role: TeamMemberRole
    status: TeamMemberStatus


async def list_my_teams(session: AsyncSession, *, user_id: uuid.UUID) -> list[MyTeam]:
    """Every team the caller is a member of (active OR pending), with their role.

    Powers the desktop "My Teams" view — where the user is captain vs plain member,
    and which requests are still awaiting approval. RLS scopes to the acting guild.
    """
    rows = (
        await session.execute(
            select(Team.id, Team.name, TeamMember.role, TeamMember.status)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == user_id, Team.is_solo.is_(False))
            .order_by(Team.name)
        )
    ).all()
    return [
        MyTeam(team_id=tid, name=name, role=role, status=status) for tid, name, role, status in rows
    ]


@dataclass(frozen=True)
class RosterMember:
    """An active roster row with the member's display name (not just their id)."""

    user_id: uuid.UUID
    role: TeamMemberRole
    display_name: str


async def list_roster(session: AsyncSession, *, team_id: uuid.UUID) -> list[RosterMember]:
    """The team's ACTIVE roster, each row named (JOINed to ``users``)."""
    await get_team(session, team_id=team_id)
    rows = (
        await session.execute(
            select(TeamMember.user_id, TeamMember.role, User.display_name)
            .join(User, User.id == TeamMember.user_id)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
            .order_by(User.display_name)
        )
    ).all()
    return [RosterMember(user_id=uid, role=role, display_name=name) for uid, role, name in rows]


@dataclass(frozen=True)
class JoinRequest:
    """A pending membership request surfaced to the captain (with joiner name)."""

    team_id: uuid.UUID
    team_name: str
    user_id: uuid.UUID
    display_name: str


async def list_join_requests(session: AsyncSession, *, team_id: uuid.UUID) -> list[JoinRequest]:
    """Pending join requests for one team (captain's approval queue), joiner-named."""
    team = await get_team(session, team_id=team_id)
    rows = (
        await session.execute(
            select(TeamMember.user_id, User.display_name)
            .join(User, User.id == TeamMember.user_id)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status == TeamMemberStatus.PENDING,
            )
            .order_by(User.display_name)
        )
    ).all()
    return [
        JoinRequest(team_id=team.id, team_name=team.name, user_id=uid, display_name=name)
        for uid, name in rows
    ]


async def list_my_join_requests(
    session: AsyncSession, *, captain_user_id: uuid.UUID
) -> list[JoinRequest]:
    """All pending requests across teams the caller CAPTAINS (desktop notify feed).

    Scoped to teams whose ``captain_user_id`` is the caller — so a captain only ever
    sees requests for their own teams. RLS already scopes to the acting guild.
    """
    rows = (
        await session.execute(
            select(
                Team.id,
                Team.name,
                TeamMember.user_id,
                User.display_name,
            )
            .join(TeamMember, TeamMember.team_id == Team.id)
            .join(User, User.id == TeamMember.user_id)
            .where(
                Team.captain_user_id == captain_user_id,
                TeamMember.status == TeamMemberStatus.PENDING,
            )
            .order_by(Team.name, User.display_name)
        )
    ).all()
    return [
        JoinRequest(team_id=tid, team_name=tname, user_id=uid, display_name=dname)
        for tid, tname, uid, dname in rows
    ]


async def approve_join_request(
    session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMember:
    """Approve a pending request → ACTIVE. Raises if there is no pending row, or
    :class:`TeamFullError` if the roster is already at ``MAX_TEAM_SIZE``."""
    await get_team(session, team_id=team_id)
    member = (
        await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == user_id,
                TeamMember.status == TeamMemberStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise MemberNotFoundError(str(user_id))
    if await _active_member_count(session, team_id) >= MAX_TEAM_SIZE:
        raise TeamFullError(str(MAX_TEAM_SIZE))
    member.status = TeamMemberStatus.ACTIVE
    await session.flush()
    await unready_registrations(session, team_id=team_id)
    return member


async def reject_join_request(
    session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Reject a pending request → delete the row. Raises if none is pending."""
    await get_team(session, team_id=team_id)
    member = (
        await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == user_id,
                TeamMember.status == TeamMemberStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise MemberNotFoundError(str(user_id))
    await session.delete(member)
    await session.flush()


async def delete_team(session: AsyncSession, *, team_id: uuid.UUID) -> None:
    """Disband a team: delete the ``teams`` row (captain-driven).

    ``team_members`` and ``registrations`` both carry ``ON DELETE CASCADE`` FKs to
    ``teams``, so the whole roster + any tournament registrations are removed by the
    database. RLS scopes the lookup to the acting guild. 404 if it does not exist.
    """
    team = await get_team(session, team_id=team_id)
    await session.delete(team)
    await session.flush()


async def _member_user_ids(session: AsyncSession, team_id: uuid.UUID) -> frozenset[uuid.UUID]:
    rows = (
        await session.execute(select(TeamMember.user_id).where(TeamMember.team_id == team_id))
    ).all()
    return frozenset(r[0] for r in rows)


async def _member_role(
    session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMemberRole | None:
    return (
        await session.execute(
            select(TeamMember.role).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def unready_registrations(session: AsyncSession, *, team_id: uuid.UUID) -> int:
    """Un-check-in the team wherever it is entered but not yet started.

    Lock-in means "every one of us is here", so growing the roster invalidates it:
    a team that locked in with four and then took a fifth would otherwise be seeded
    into a bracket carrying a player who never confirmed. The newcomer (and only
    they) then has to press Lock in, which flips the registration back.

    Scoped to tournaments that have not started — a running bracket's registrations
    are history and must not be rewritten under it.
    """
    unstarted = {
        t.id
        for t in (await session.execute(select(Tournament))).scalars().all()
        if t.status in (TournamentStatus.DRAFT, TournamentStatus.OPEN, TournamentStatus.CHECK_IN)
    }
    regs = [
        reg
        for reg in (
            await session.execute(
                select(Registration).where(
                    Registration.team_id == team_id,
                    Registration.status == RegistrationStatus.CHECKED_IN,
                )
            )
        )
        .scalars()
        .all()
        if reg.tournament_id in unstarted
    ]
    for reg in regs:
        reg.status = RegistrationStatus.PENDING
    if regs:
        await session.flush()
    return len(regs)


async def add_member(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    role: TeamMemberRole = TeamMemberRole.PLAYER,
    status: TeamMemberStatus = TeamMemberStatus.ACTIVE,
) -> TeamMember:
    """Add a player/sub to a team. Validation is delegated to the pure helper.

    ``status`` defaults to ACTIVE (a captain directly adding someone). The code
    self-service path passes PENDING so the captain must approve first.
    """
    team = await get_team(session, team_id=team_id)
    existing = await _member_user_ids(session, team_id)
    change = validate_add_member(user_id=user_id, role=role, existing_member_user_ids=existing)
    member = TeamMember(
        team_id=team_id,
        user_id=change.user_id,
        guild_id=team.guild_id,  # same-guild by construction (FK target's guild)
        role=change.role,
        status=status,
    )
    session.add(member)
    await session.flush()
    if status == TeamMemberStatus.ACTIVE:
        await unready_registrations(session, team_id=team_id)
    return member


async def remove_member(session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Remove a member from a team (the captain is protected — transfer first)."""
    team = await get_team(session, team_id=team_id)
    role = await _member_role(session, team_id=team_id, user_id=user_id)
    validate_remove_member(user_id=user_id, captain_user_id=team.captain_user_id, member_role=role)
    member = (
        await session.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).scalar_one()
    await session.delete(member)
    await session.flush()


async def leave_team(session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """A member removes themselves. Same captain guard as :func:`remove_member`."""
    await remove_member(session, team_id=team_id, user_id=user_id)


async def transfer_captain(
    session: AsyncSession, *, team_id: uuid.UUID, new_captain_user_id: uuid.UUID
) -> Team:
    """Hand team captaincy to another ACTIVE member (edge-cases §1.4).

    Resolves the CaptainProtectedError dead-end: a captain who wants to leave
    (without disbanding) transfers captaincy first. The old captain is demoted to
    ``player`` (still a member) and the new captain's membership is promoted; the
    team's ``captain_user_id`` is repointed. Idempotent — transferring to the
    current captain is a no-op.

    Raises:
        TeamNotFoundError: no such team in the acting guild.
        NewCaptainNotEligibleError: the target is not an ACTIVE member of the team.
    """
    team = await get_team(session, team_id=team_id)
    if team.captain_user_id == new_captain_user_id:
        return team  # already the captain — no-op.

    new_member = (
        await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == new_captain_user_id,
            )
        )
    ).scalar_one_or_none()
    if new_member is None or new_member.status != TeamMemberStatus.ACTIVE:
        raise NewCaptainNotEligibleError(str(new_captain_user_id))

    old_member = (
        await session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == team.captain_user_id,
            )
        )
    ).scalar_one_or_none()
    if old_member is not None:
        old_member.role = TeamMemberRole.PLAYER
    new_member.role = TeamMemberRole.CAPTAIN
    team.captain_user_id = new_captain_user_id
    await session.flush()
    logger.info(
        "team.captain_transferred",
        team_id=str(team_id),
        new_captain=str(new_captain_user_id),
    )
    return team
