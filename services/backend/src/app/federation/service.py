"""Central-feed federation service: consent → projection → approval (PLAN.md §3.2).

This is the data core behind :mod:`app.federation.router`. It implements the
**one-way, consented, admin-approved, field-whitelisted** projection of a guild's
tournament into the shared *League Of Leagues* central feed (PLAN.md §3.2 / §0
"surfaced (with consent + approval)"; §9 privacy):

* :func:`request_share` — a **guild_admin** opts a tournament in
  (``is_shared_to_central = True``) and a *pending* :class:`~app.models.
  CentralFeedItem` is created carrying ONLY the whitelisted projection. Requires
  the guild's ``central_feed_enabled`` opt-in (per-guild consent, §3.2).
* :func:`approve_share` — a **platform_operator** (central staff) approves a
  pending item → *published* (``approved_at``/``approved_by`` + ``published_at``).
* :func:`revoke_share` — sets ``revoked_at`` so the item disappears from the feed.
* :func:`list_central_feed` — returns **published, non-revoked** items only.

The projection (:func:`project_tournament`) is the privacy boundary (PLAN.md §9):
it copies a CLOSED set of safe fields (tournament name, status, team count, source
guild display name) and NEVER PUUID, Discord ids, or verification state. Per-player
:class:`~app.models.PlayerCentralVisibility` is honoured — a player's Riot id is
included ONLY when that player set ``show_riot_id`` for the tournament (default
``team_only`` ⇒ excluded). :data:`SENSITIVE_FIELD_KEYS` is a defence-in-depth deny
list asserted on the projected dict so an accidental sensitive key fails loudly.

No live cross-tenant query exists: the central feed reads ONLY the projected rows
(the ``central_feed_items`` table), never another guild's live tournament data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import (
    CentralFeedItem,
    Guild,
    PlayerCentralVisibility,
    Registration,
    RiotAccount,
    Tournament,
)

log = get_logger("app.federation")

#: Per-player visibility values (mirror the ``player_central_visibility`` CHECK
#: constraint, PLAN.md §2). ``team_only`` (the default) hides the Riot id from the
#: central feed; only ``show_riot_id`` opts the player's Riot id in.
VISIBILITY_TEAM_ONLY = "team_only"
VISIBILITY_SHOW_RIOT_ID = "show_riot_id"

#: Defence-in-depth deny list (PLAN.md §9): keys that must NEVER appear in a
#: projected ``central_feed_items.projected_fields`` blob. Matched case-insensitively
#: as substrings so e.g. ``encrypted_puuid`` / ``discord_user_id`` are also caught.
#: The projection is whitelist-first; this assertion is a loud second gate.
SENSITIVE_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "puuid",
        "discord_id",
        "discord_user_id",
        "discord_guild_id",
        "user_id",
        "summoner_id",
        "summoner_name",
        "verified",
        "verification",
    }
)


class FederationError(Exception):
    """Base class for central-feed federation failures."""


class FeedNotEnabledError(FederationError):
    """The source guild has not opted in (``central_feed_enabled`` is false)."""


class FeedItemNotFoundError(FederationError):
    """No central-feed item with that id (or it is not in the expected state)."""


class NotCentralStaffError(FederationError):
    """The acting principal is not central staff (platform_operator) — cannot approve."""


class AlreadyApprovedError(FederationError):
    """The item is already published — approval is not idempotent re-approvable."""


class SensitiveProjectionError(FederationError):
    """A projected field carried a forbidden identifier (defence-in-depth, §9)."""


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Projection (the privacy boundary — PLAN.md §9, whitelist-first)
# ---------------------------------------------------------------------------
def _assert_no_sensitive_keys(projected: dict[str, str]) -> None:
    """Fail closed if any projected key matches the sensitive deny list (§9).

    The projection is built field-by-field from a closed whitelist, so this can
    only trip if the whitelist itself is ever changed to include a forbidden key —
    it turns that mistake into a loud error instead of a silent PII leak.
    """
    for key in projected:
        lowered = key.lower()
        if any(bad in lowered for bad in SENSITIVE_FIELD_KEYS):
            raise SensitiveProjectionError(f"forbidden field in projection: {key!r}")


async def _team_count(session: AsyncSession, tournament_id: uuid.UUID) -> int:
    """Count registrations for a tournament (the published "team count")."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Registration)
            .where(Registration.tournament_id == tournament_id)
        )
    ).scalar_one()


async def _consented_riot_ids(session: AsyncSession, tournament_id: uuid.UUID) -> list[str]:
    """Riot ids of players who set ``show_riot_id`` for this tournament (§9 consent).

    Default visibility is ``team_only`` (no row, or an explicit ``team_only`` row) ⇒
    the player is NOT surfaced. Only an explicit ``show_riot_id`` row opts the
    player's *display* Riot id (gameName#tagLine) in — never the PUUID, summoner id,
    or verification state. Players with no resolved Riot id are skipped.
    """
    opted_in = (
        (
            await session.execute(
                select(PlayerCentralVisibility.user_id).where(
                    PlayerCentralVisibility.tournament_id == tournament_id,
                    PlayerCentralVisibility.visibility == VISIBILITY_SHOW_RIOT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    riot_ids: list[str] = []
    for user_id in opted_in:
        account = (
            await session.execute(select(RiotAccount).where(RiotAccount.user_id == user_id))
        ).scalar_one_or_none()
        if account is None:
            continue
        game_name = account.riot_id_game_name
        tag_line = account.riot_id_tag_line
        if not game_name:
            continue
        riot_ids.append(f"{game_name}#{tag_line}" if tag_line else game_name)
    return riot_ids


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


async def project_tournament(
    session: AsyncSession,
    *,
    tournament: Tournament,
    source_guild: Guild,
) -> dict[str, str]:
    """Build the field-whitelisted projection for a tournament (PLAN.md §3.2/§9).

    Returns a flat ``dict[str, str]`` (mirrors ``CentralFeedItem.projected_fields``)
    containing ONLY safe display data:

    * ``tournament_name`` — the tournament's display name,
    * ``status`` — its lifecycle status (e.g. ``open``/``running``),
    * ``team_count`` — number of registered teams (stringified),
    * ``source_guild_name`` — the source guild's display name,
    * ``shared_riot_ids`` — comma-joined Riot ids of players who explicitly chose
      ``show_riot_id`` (omitted entirely when none consented).

    It NEVER includes PUUID, Discord ids, summoner ids, or verification state. The
    result is asserted against :data:`SENSITIVE_FIELD_KEYS` before return (§9).
    """
    team_count = await _team_count(session, tournament.id)
    consented = await _consented_riot_ids(session, tournament.id)

    projected: dict[str, str] = {
        "tournament_name": tournament.name,
        "status": _enum_value(tournament.status),
        "team_count": str(team_count),
        "source_guild_name": source_guild.name,
    }
    if consented:
        projected["shared_riot_ids"] = ", ".join(consented)

    _assert_no_sensitive_keys(projected)
    return projected


# ---------------------------------------------------------------------------
# Request → approve → revoke lifecycle
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FeedItemView:
    """A read view of a central-feed item (no raw model leakage to the router)."""

    id: str
    source_guild_id: str
    tournament_id: str
    projected_fields: dict[str, str]
    status: str
    published_at: str | None
    approved_at: str | None
    revoked_at: str | None


def _status_of(item: CentralFeedItem) -> str:
    """Derive a coarse lifecycle status from the timestamp columns."""
    if item.revoked_at is not None:
        return "revoked"
    if item.published_at is not None and item.approved_at is not None:
        return "published"
    return "pending"


def _view(item: CentralFeedItem) -> FeedItemView:
    return FeedItemView(
        id=str(item.id),
        source_guild_id=str(item.source_guild_id),
        tournament_id=str(item.tournament_id),
        projected_fields=dict(item.projected_fields),
        status=_status_of(item),
        published_at=item.published_at.isoformat() if item.published_at else None,
        approved_at=item.approved_at.isoformat() if item.approved_at else None,
        revoked_at=item.revoked_at.isoformat() if item.revoked_at else None,
    )


async def request_share(
    session: AsyncSession,
    *,
    tournament: Tournament,
    source_guild: Guild,
    requested_by: uuid.UUID,
) -> FeedItemView:
    """Opt a tournament into the central feed → a PENDING projected item (§3.2).

    Pre-conditions enforced by the caller (router/bridge): the actor is a
    ``guild_admin`` of ``source_guild`` and the tenant GUC is set. This function
    enforces the per-guild **consent** gate (``central_feed_enabled``), sets
    ``is_shared_to_central`` on the tournament, and inserts a pending
    :class:`~app.models.CentralFeedItem` carrying ONLY the whitelisted projection.

    Idempotent: re-requesting an already-shared tournament refreshes the pending
    item's projection rather than creating a duplicate (a published item is left
    untouched — re-approval is a separate, deliberate central-staff action).

    Raises:
        FeedNotEnabledError: the source guild has not opted in.
    """
    settings = source_guild.settings or {}
    if not settings.get("central_feed_enabled", False):
        raise FeedNotEnabledError(str(source_guild.id))

    projected = await project_tournament(session, tournament=tournament, source_guild=source_guild)

    tournament.is_shared_to_central = True

    existing = (
        await session.execute(
            select(CentralFeedItem).where(
                CentralFeedItem.tournament_id == tournament.id,
                CentralFeedItem.source_guild_id == source_guild.id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None and existing.revoked_at is None:
        # Refresh the pending projection in place (no duplicate row).
        existing.projected_fields = projected
        if existing.published_at is None:
            existing.published_by = requested_by
        await session.flush()
        log.info(
            "central_feed_share_refreshed",
            item_id=str(existing.id),
            tournament_id=str(tournament.id),
            source_guild_id=str(source_guild.id),
        )
        return _view(existing)

    item = CentralFeedItem(
        source_guild_id=source_guild.id,
        tournament_id=tournament.id,
        projected_fields=projected,
        published_by=requested_by,
    )
    session.add(item)
    await session.flush()
    log.info(
        "central_feed_share_requested",
        item_id=str(item.id),
        tournament_id=str(tournament.id),
        source_guild_id=str(source_guild.id),
    )
    return _view(item)


async def _get_item(session: AsyncSession, *, item_id: uuid.UUID) -> CentralFeedItem:
    item = (
        await session.execute(select(CentralFeedItem).where(CentralFeedItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise FeedItemNotFoundError(str(item_id))
    return item


async def approve_share(
    session: AsyncSession,
    *,
    item_id: uuid.UUID,
    approved_by: uuid.UUID,
    is_platform_operator: bool,
) -> FeedItemView:
    """Central staff approves a pending item → PUBLISHED (PLAN.md §3.2/§784).

    Only a ``platform_operator`` (central staff) may approve — inbound central-feed
    abuse is gated on staff approval (PLAN.md §784 risk row). Sets ``approved_by``/
    ``approved_at`` and stamps ``published_at`` so the item becomes visible in
    :func:`list_central_feed`.

    Raises:
        NotCentralStaffError: the actor is not a platform operator.
        FeedItemNotFoundError: no such item.
        AlreadyApprovedError: the item is already published.
    """
    if not is_platform_operator:
        raise NotCentralStaffError(str(approved_by))

    item = await _get_item(session, item_id=item_id)
    if item.revoked_at is not None:
        raise FeedItemNotFoundError(str(item_id))
    if item.approved_at is not None and item.published_at is not None:
        raise AlreadyApprovedError(str(item_id))

    now = _now()
    item.approved_by = approved_by
    item.approved_at = now
    item.published_at = now
    await session.flush()
    log.info("central_feed_share_approved", item_id=str(item.id), approved_by=str(approved_by))
    return _view(item)


async def revoke_share(
    session: AsyncSession,
    *,
    item_id: uuid.UUID,
    is_platform_operator: bool,
) -> FeedItemView:
    """Revoke a central-feed item (sets ``revoked_at``) — hides it from the feed.

    A ``platform_operator`` may revoke any item (central-staff moderation). Revoke
    is idempotent: revoking an already-revoked item is a clean no-op returning the
    current view.

    Raises:
        NotCentralStaffError: the actor is not a platform operator.
        FeedItemNotFoundError: no such item.
    """
    if not is_platform_operator:
        raise NotCentralStaffError("revoke requires platform_operator")

    item = await _get_item(session, item_id=item_id)
    if item.revoked_at is None:
        item.revoked_at = _now()
        await session.flush()
        log.info("central_feed_share_revoked", item_id=str(item.id))
    return _view(item)


async def list_central_feed(session: AsyncSession) -> list[FeedItemView]:
    """Return PUBLISHED, non-revoked central-feed items (PLAN.md §3.2).

    The central feed is built EXCLUSIVELY from the projected rows — there is no live
    cross-tenant query into another guild's tournament data (PLAN.md §9). Filters to
    items that have been approved + published and not subsequently revoked.
    """
    rows = (
        (
            await session.execute(
                select(CentralFeedItem)
                .where(
                    CentralFeedItem.published_at.is_not(None),
                    CentralFeedItem.approved_at.is_not(None),
                    CentralFeedItem.revoked_at.is_(None),
                )
                .order_by(CentralFeedItem.published_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_view(item) for item in rows]


# ---------------------------------------------------------------------------
# Resolution helpers (tournament by id within the acting guild)
# ---------------------------------------------------------------------------
async def get_tournament_for_share(
    session: AsyncSession, *, tournament_id: uuid.UUID
) -> Tournament:
    """Load a tournament in the acting guild (RLS-scoped) for a share request."""
    t = (
        await session.execute(select(Tournament).where(Tournament.id == tournament_id))
    ).scalar_one_or_none()
    if t is None:
        raise FeedItemNotFoundError(f"tournament {tournament_id}")
    return t


__all__ = [
    "SENSITIVE_FIELD_KEYS",
    "VISIBILITY_SHOW_RIOT_ID",
    "VISIBILITY_TEAM_ONLY",
    "AlreadyApprovedError",
    "FederationError",
    "FeedItemNotFoundError",
    "FeedItemView",
    "FeedNotEnabledError",
    "NotCentralStaffError",
    "SensitiveProjectionError",
    "approve_share",
    "get_tournament_for_share",
    "list_central_feed",
    "project_tournament",
    "request_share",
    "revoke_share",
]
