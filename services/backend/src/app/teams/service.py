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

from lol_schemas.enums import TeamMemberRole
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.joincodes import generate_join_code, normalize_join_code
from app.models import Team, TeamMember

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
    return list((await session.execute(select(Team))).scalars().all())


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
    """Add the acting user to a team by its join code (idempotent as a PLAYER).

    Redeeming a code you already hold membership for returns the existing row
    rather than raising :class:`DuplicateMemberError` — a friendly no-op for a
    double-paste. Captains keep their captain membership (never downgraded).
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
    member = await add_member(session, team_id=team.id, user_id=user_id, role=TeamMemberRole.PLAYER)
    return team, member


async def list_members(session: AsyncSession, *, team_id: uuid.UUID) -> list[TeamMember]:
    """List a team's members (RLS-scoped). Verifies the team exists in this guild."""
    await get_team(session, team_id=team_id)
    rows = (
        await session.execute(
            select(TeamMember).where(TeamMember.team_id == team_id).order_by(TeamMember.user_id)
        )
    ).scalars()
    return list(rows)


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


async def add_member(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    role: TeamMemberRole = TeamMemberRole.PLAYER,
) -> TeamMember:
    """Add a player/sub to a team. Validation is delegated to the pure helper."""
    team = await get_team(session, team_id=team_id)
    existing = await _member_user_ids(session, team_id)
    change = validate_add_member(user_id=user_id, role=role, existing_member_user_ids=existing)
    member = TeamMember(
        team_id=team_id,
        user_id=change.user_id,
        guild_id=team.guild_id,  # same-guild by construction (FK target's guild)
        role=change.role,
    )
    session.add(member)
    await session.flush()
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
