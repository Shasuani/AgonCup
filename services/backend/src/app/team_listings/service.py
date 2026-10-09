"""Team listings: "looking for players", across every server (PLAN.md §3.1).

A captain posts their team with a region and a short message; a player on ANY
server that runs AgonCup sees it in the desktop client's "Find a team" screen and
asks to join. The request is the ordinary pending membership the join-code path
already creates, so the captain approves or rejects it exactly as before.

Tenancy — read this before changing anything here
=================================================
Teams live under per-guild RLS; a listing does not (:class:`~app.models.TeamListing`
is a global table, like the central feed). Two things therefore happen on a guild
the caller may hold no role in, both server-side and both scoped to listed teams:

* **Reading** — :func:`list_listings` re-points ``app.current_guild`` at each
  listed team's server in turn to read that team's name and roster. It only ever
  selects the teams named by a listing (the captain published them), and the
  acting guild is restored afterwards.
* **Requesting** — :func:`request_to_join` points the transaction at the team's
  server (taken from the listing row, never from the request), gives the
  requester a baseline ``player`` role there if they have none — the same role
  ``/register`` grants on a server, and what lets their client address the team
  (roster, leave) with ``X-Guild-Id`` afterwards — and writes the pending
  membership there.

Everything else stays under the caller's own tenant session.

Lifetime
========
A post lives only while its poster's desktop client is open: the client calls
:func:`keep_alive` every minute, and a post not heard from for
:data:`LISTING_TTL` (5 minutes — room for a client restart) is stale. A stale
post is never shown, never revived, and is deleted the next time anything reads
it (owner + Max, 2026-10-07).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lol_riot.routing import PlatformRoute
from lol_schemas.enums import TeamMemberRole, TeamMemberStatus, UserGuildRoleName
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import format_rank_fields, region_label_for
from app.auth.tenancy import current_guild_id, set_acting_guild
from app.logging import get_logger
from app.models import Guild, RiotAccount, Team, TeamListing, TeamMember, User, UserGuildRole
from app.teams import service as teams

logger = get_logger(__name__)

#: Longest message a captain can attach ("need a support, Gold+, EU evenings").
MAX_MESSAGE_LENGTH = 200

#: How long a post outlives its poster's last keep-alive. The client pings every
#: minute, so this is a few missed pings or a restart, not a closed client.
LISTING_TTL = timedelta(minutes=5)


class ListingError(Exception):
    """Base class for team-listing failures."""


class ListingNotFoundError(ListingError):
    """No listing with that id (or it was taken down)."""


class ListingExistsError(ListingError):
    """The team already has an open listing — delete it before posting again."""


class NotTeamCaptainError(ListingError):
    """Only the team's captain may post (or take down) its listing."""


class InvalidRegionError(ListingError):
    """Not a Riot platform route we support (KR is out of scope)."""


class RegionAccountRequiredError(ListingError):
    """The caller holds no verified account on the listing's server.

    An LCU invite is addressed per server, so a player without an account there
    could join the team and still never be pulled into its lobby.
    """

    def __init__(self, region_label: str) -> None:
        self.region_label = region_label
        super().__init__(f"you need a verified {region_label} account for this")


def parse_listing_region(region: str) -> str:
    """Validate a listing region → canonical :class:`PlatformRoute` value (no KR)."""
    try:
        platform = PlatformRoute(region.strip().lower())
    except ValueError as exc:
        raise InvalidRegionError(f"unknown region: {region!r}") from exc
    if platform is PlatformRoute.KR:
        raise InvalidRegionError("region kr is not supported")
    return platform.value


def clean_message(message: str | None) -> str | None:
    """Trim the captain's message; blank → None; over the limit → error."""
    if message is None:
        return None
    text = " ".join(message.split())
    if not text:
        return None
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ListingError(f"message is longer than {MAX_MESSAGE_LENGTH} characters")
    return text


async def _require_region_account(
    session: AsyncSession, *, user_id: uuid.UUID, region: str
) -> None:
    """Raise unless ``user_id`` holds a verified Riot account on ``region``."""
    found = (
        await session.execute(
            select(RiotAccount.id).where(
                RiotAccount.user_id == user_id,
                RiotAccount.verified.is_(True),
                RiotAccount.region == region,
            )
        )
    ).first()
    if found is None:
        raise RegionAccountRequiredError(region_label_for(PlatformRoute(region)))


def _is_stale(listing: TeamListing, now: datetime) -> bool:
    # Unset = just built in this transaction (the DB fills the default at INSERT).
    return listing.last_seen_at is not None and listing.last_seen_at < now - LISTING_TTL


async def _drop_stale(session: AsyncSession, listings: list[TeamListing]) -> list[TeamListing]:
    """Delete the stale posts among ``listings``; return the live ones."""
    now = datetime.now(UTC)
    live: list[TeamListing] = []
    for listing in listings:
        if _is_stale(listing, now):
            await session.delete(listing)
            logger.info("team_listing_expired", listing_id=str(listing.id))
        else:
            live.append(listing)
    if len(live) != len(listings):
        await session.flush()
    return live


async def _get_listing(session: AsyncSession, listing_id: uuid.UUID) -> TeamListing:
    listing = (
        await session.execute(select(TeamListing).where(TeamListing.id == listing_id))
    ).scalar_one_or_none()
    if listing is None or not await _drop_stale(session, [listing]):
        raise ListingNotFoundError(str(listing_id))
    return listing


# ---------------------------------------------------------------------------
# Post / take down (captain; admin may take down)
# ---------------------------------------------------------------------------
async def create_listing(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    region: str,
    message: str | None,
) -> TeamListing:
    """Post the caller's team. Runs on the TEAM's tenant (the client sends its guild).

    The caller must be the team's captain, hold a verified account on ``region``
    (the captain's client hosts lobbies there), and the team must have a free
    slot. One listing per team: a second post raises :class:`ListingExistsError`.
    """
    team = await teams.get_team(session, team_id=team_id)
    if team.is_solo:
        raise teams.TeamNotFoundError(str(team_id))
    if team.captain_user_id != user_id:
        raise NotTeamCaptainError(str(team_id))
    platform = parse_listing_region(region)
    text = clean_message(message)
    await _require_region_account(session, user_id=user_id, region=platform)
    if await teams.active_member_count(session, team.id) >= teams.MAX_TEAM_SIZE:
        raise teams.TeamFullError(str(teams.MAX_TEAM_SIZE))
    existing = list(
        (await session.execute(select(TeamListing).where(TeamListing.team_id == team.id))).scalars()
    )
    # A stale post (its client went away) is cleared, not a reason to refuse.
    if await _drop_stale(session, existing):
        raise ListingExistsError(str(team.id))
    listing = TeamListing(
        team_id=team.id,
        guild_id=team.guild_id,
        region=platform,
        message=text,
        created_by_user_id=user_id,
        last_seen_at=datetime.now(UTC),
    )
    session.add(listing)
    await session.flush()
    logger.info("team_listing_created", team_id=str(team.id), region=platform)
    return listing


async def delete_listing(
    session: AsyncSession,
    *,
    listing_id: uuid.UUID,
    user_id: uuid.UUID,
    is_platform_operator: bool,
) -> None:
    """Take a listing down: its team's captain, or a platform operator (owner rule).

    The captain check reads the team on ITS server (the listing names it), so it
    works whichever server the caller's client is acting on.
    """
    listing = await _get_listing(session, listing_id)
    if not is_platform_operator:
        origin = await current_guild_id(session)
        await set_acting_guild(session, str(listing.guild_id))
        try:
            team = (
                await session.execute(select(Team).where(Team.id == listing.team_id))
            ).scalar_one_or_none()
        finally:
            if origin is not None:
                await set_acting_guild(session, origin)
        if team is None or team.captain_user_id != user_id:
            raise NotTeamCaptainError(str(listing.team_id))
    await session.delete(listing)
    await session.flush()
    logger.info(
        "team_listing_deleted", listing_id=str(listing_id), by_operator=is_platform_operator
    )


async def keep_alive(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """The poster's client is still open: refresh every live post they made.

    Called every minute by the desktop client, whoever is signed in — a user
    with no post costs one empty SELECT. A post already stale is deleted, not
    revived: closing the client for longer than :data:`LISTING_TTL` takes the
    team off the list for good. Returns how many posts were kept alive.
    """
    mine = list(
        (
            await session.execute(
                select(TeamListing).where(TeamListing.created_by_user_id == user_id)
            )
        ).scalars()
    )
    live = await _drop_stale(session, mine)
    now = datetime.now(UTC)
    for listing in live:
        listing.last_seen_at = now
    if live:
        await session.flush()
    return len(live)


# ---------------------------------------------------------------------------
# The "Find a team" list
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ListingMember:
    """One active roster row as a stranger sees it: a name, a role, a rank."""

    display_name: str
    role: TeamMemberRole
    #: Solo/Duo snapshot on the listing's server, e.g. "Gold II, 45 LP".
    rank_text: str


@dataclass(frozen=True, slots=True)
class ListingView:
    """One listing with its team read live from the team's server."""

    listing_id: uuid.UUID
    team_id: uuid.UUID
    guild_id: uuid.UUID
    team_name: str
    server_name: str
    region: str
    message: str | None
    created_at: datetime
    members: list[ListingMember]
    #: "captain" / "member" / "pending" — the VIEWER's tie to this team, or None.
    viewer_status: str | None
    #: Whether the viewer may take this listing down (captain or operator).
    can_remove: bool

    @property
    def member_count(self) -> int:
        return len(self.members)


async def list_listings(
    session: AsyncSession,
    *,
    viewer_id: uuid.UUID,
    is_platform_operator: bool,
    region: str | None = None,
) -> list[ListingView]:
    """Every open listing (optionally one region's), newest first.

    Stale posts (the poster's client went away) are deleted on the way. Full teams are left out even if their listing is still there (a captain can
    also fill a roster by code), and so are listings whose team has vanished.
    """
    query = select(TeamListing)
    if region is not None:
        query = query.where(TeamListing.region == parse_listing_region(region))
    listings = await _drop_stale(session, list((await session.execute(query)).scalars().all()))
    if not listings:
        return []

    by_guild: dict[uuid.UUID, list[TeamListing]] = defaultdict(list)
    for listing in listings:
        by_guild[listing.guild_id].append(listing)

    teams_by_id: dict[uuid.UUID, Team] = {}
    members_by_team: dict[uuid.UUID, list[TeamMember]] = defaultdict(list)
    origin = await current_guild_id(session)
    try:
        for guild_id, guild_listings in by_guild.items():
            await set_acting_guild(session, str(guild_id))
            ids = [row.team_id for row in guild_listings]
            for found in (await session.execute(select(Team).where(Team.id.in_(ids)))).scalars():
                teams_by_id[found.id] = found
            for member in (
                await session.execute(select(TeamMember).where(TeamMember.team_id.in_(ids)))
            ).scalars():
                members_by_team[member.team_id].append(member)
    finally:
        if origin is not None:
            await set_acting_guild(session, origin)

    user_ids = {m.user_id for ms in members_by_team.values() for m in ms}
    names: dict[uuid.UUID, str] = {
        row[0]: row[1]
        for row in (
            await session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
        ).all()
    }
    accounts = list(
        (
            await session.execute(
                select(RiotAccount).where(
                    RiotAccount.user_id.in_(user_ids), RiotAccount.verified.is_(True)
                )
            )
        ).scalars()
    )
    server_names: dict[uuid.UUID, str] = {
        row[0]: row[1]
        for row in (
            await session.execute(select(Guild.id, Guild.name).where(Guild.id.in_(list(by_guild))))
        ).all()
    }

    def _rank(user_id: uuid.UUID, on_region: str) -> str:
        for account in accounts:
            if account.user_id == user_id and account.region == on_region:
                return format_rank_fields(
                    tier=account.rank_tier,
                    division=account.rank_division,
                    league_points=account.rank_lp,
                )
        return "Unranked"

    views: list[ListingView] = []
    for listing in listings:
        team = teams_by_id.get(listing.team_id)
        if team is None:
            continue
        roster = members_by_team.get(team.id, [])
        active = [m for m in roster if m.status == TeamMemberStatus.ACTIVE]
        if len(active) >= teams.MAX_TEAM_SIZE:
            continue
        mine = next((m for m in roster if m.user_id == viewer_id), None)
        if mine is None:
            viewer_status = None
        elif mine.status == TeamMemberStatus.PENDING:
            viewer_status = "pending"
        elif team.captain_user_id == viewer_id:
            viewer_status = "captain"
        else:
            viewer_status = "member"
        members = sorted(
            (
                ListingMember(
                    display_name=names.get(m.user_id, "Player"),
                    role=TeamMemberRole.CAPTAIN if m.user_id == team.captain_user_id else m.role,
                    rank_text=_rank(m.user_id, listing.region),
                )
                for m in active
            ),
            # Captain first, then by name.
            key=lambda m: (m.role != TeamMemberRole.CAPTAIN, m.display_name.lower()),
        )
        views.append(
            ListingView(
                listing_id=listing.id,
                team_id=team.id,
                guild_id=team.guild_id,
                team_name=team.name,
                server_name=server_names.get(team.guild_id, "Discord server"),
                region=listing.region,
                message=listing.message,
                created_at=listing.created_at,
                members=members,
                viewer_status=viewer_status,
                can_remove=is_platform_operator or team.captain_user_id == viewer_id,
            )
        )
    views.sort(key=lambda v: v.created_at, reverse=True)
    return views


# ---------------------------------------------------------------------------
# Ask to join
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class JoinRequestResult:
    team: Team
    member: TeamMember


async def request_to_join(
    session: AsyncSession, *, listing_id: uuid.UUID, user_id: uuid.UUID
) -> JoinRequestResult:
    """Ask to join a listed team — a PENDING membership on the team's server.

    Idempotent: asking again (or being on the team already) returns the existing
    row. The transaction is LEFT on the team's server, where the rows were written.
    """
    listing = await _get_listing(session, listing_id)
    await _require_region_account(session, user_id=user_id, region=listing.region)

    await set_acting_guild(session, str(listing.guild_id))
    team = await teams.get_team(session, team_id=listing.team_id)
    existing = (
        await session.execute(
            select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return JoinRequestResult(team=team, member=existing)
    if await teams.active_member_count(session, team.id) >= teams.MAX_TEAM_SIZE:
        raise teams.TeamFullError(str(teams.MAX_TEAM_SIZE))

    has_role = (
        await session.execute(
            select(UserGuildRole.id).where(
                UserGuildRole.user_id == user_id,
                UserGuildRole.guild_id == listing.guild_id,
            )
        )
    ).first()
    if has_role is None:
        session.add(
            UserGuildRole(user_id=user_id, guild_id=listing.guild_id, role=UserGuildRoleName.PLAYER)
        )
        await session.flush()

    member = await teams.add_member(
        session,
        team_id=team.id,
        user_id=user_id,
        role=TeamMemberRole.PLAYER,
        status=TeamMemberStatus.PENDING,
    )
    logger.info("team_listing_join_requested", listing_id=str(listing_id), team_id=str(team.id))
    return JoinRequestResult(team=team, member=member)
