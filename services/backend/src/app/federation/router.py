"""Federation router: consented central-feed projection + approval (PLAN.md §3.2).

Service-authenticated HTTP surface the Discord bot calls (it holds the only copy of
``bot_service_token`` — see :mod:`app.integrations.discord.service_auth`). It exposes
the **one-way, consented, admin-approved, field-whitelisted** central-feed pipeline
(PLAN.md §3.2 / §0 "surfaced (with consent + approval)"; §9 privacy):

* ``POST /federation/share/request`` — a **guild_admin** of the source guild opts a
  tournament in (``is_shared_to_central``) and a *pending* projected
  :class:`~app.models.CentralFeedItem` is created (requires the guild's
  ``central_feed_enabled`` consent).
* ``POST /federation/share/approve`` — **central staff** (admin of the
  ``is_central`` guild) approves a pending item → *published*.
* ``POST /federation/share/revoke`` — central staff revokes an item (``revoked_at``).
* ``GET  /federation/feed`` — the **published, non-revoked** feed (read-only).

Authority model (no user JWT — the bot is the token holder, PLAN.md §3.2): the bot
asserts the Discord interaction context; admin gating reuses
:func:`app.guilds.service.ensure_guild_admin` for the source guild (request) and,
for approve/revoke, requires the actor be an admin of the central guild
(``Guild.is_central``) — the bot-path analogue of ``platform_operator``. No domain
logic lives here; it DELEGATES to :mod:`app.federation.service`, the privacy
boundary. The feed reads ONLY the projected rows — never another guild's live data.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.federation import service as federation
from app.guilds import service as guilds_service
from app.integrations.discord.service import DiscordContext
from app.integrations.discord.service_auth import require_service_token
from app.logging import get_logger

router = APIRouter(
    prefix="/federation",
    tags=["federation"],
    dependencies=[Depends(require_service_token)],
)
log = get_logger("app.federation")


# ---------------------------------------------------------------------------
# Request / response models (the asserted Discord ctx mirrors the bridge)
# ---------------------------------------------------------------------------
class DiscordContextIn(BaseModel):
    """The asserted Discord interaction context (trusted: only the bot has the token)."""

    discord_user_id: int = Field(..., description="Invoking member's snowflake.")
    discord_guild_id: int = Field(..., description="Guild the command ran in.")
    is_discord_guild_admin: bool = Field(
        default=False, description="The member's Manage-Guild permission (bot-asserted)."
    )
    display_name: str | None = Field(default=None, description="Discord display name.")

    def to_ctx(self) -> DiscordContext:
        return DiscordContext(
            discord_user_id=self.discord_user_id,
            discord_guild_id=self.discord_guild_id,
            is_discord_guild_admin=self.is_discord_guild_admin,
            display_name=self.display_name,
        )


class ShareRequest(BaseModel):
    ctx: DiscordContextIn
    tournament_id: uuid.UUID


class FeedItemRefRequest(BaseModel):
    ctx: DiscordContextIn
    item_id: uuid.UUID


class FeedItemResponse(BaseModel):
    id: str
    source_guild_id: str
    tournament_id: str
    projected_fields: dict[str, str]
    status: str
    published_at: str | None
    approved_at: str | None
    revoked_at: str | None

    @classmethod
    def of(cls, view: federation.FeedItemView) -> FeedItemResponse:
        return cls(
            id=view.id,
            source_guild_id=view.source_guild_id,
            tournament_id=view.tournament_id,
            projected_fields=view.projected_fields,
            status=view.status,
            published_at=view.published_at,
            approved_at=view.approved_at,
            revoked_at=view.revoked_at,
        )


# ---------------------------------------------------------------------------
# Error mapping (domain → HTTP)
# ---------------------------------------------------------------------------
def _guild_not_onboarded(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="guild is not onboarded; invite/sync the bot first",
    )


def _not_central_staff(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="central-feed approval requires central staff",
    )


async def _require_central_admin(session: AsyncSession, ctx: DiscordContext) -> None:
    """Gate an approve/revoke action on central-staff authority (PLAN.md §3.2/§784).

    The bot-path analogue of ``platform_operator``: the acting Discord guild must be
    the central guild (``Guild.is_central``) and the actor must be a guild admin of
    it (Manage-Guild OR a persisted ``guild_admin`` role). Resolves the user+guild,
    sets the tenant GUC, then enforces admin gating.
    """
    user = await guilds_service.upsert_user_by_discord_id(
        session,
        discord_user_id=ctx.discord_user_id,
        display_name=ctx.display_name,
    )
    guild = await guilds_service.resolve_guild_by_discord_id(
        session, discord_guild_id=ctx.discord_guild_id
    )
    if not guild.is_central:
        raise federation.NotCentralStaffError(str(guild.id))
    await guilds_service.set_tenant_guild(session, guild_id=guild.id)
    await guilds_service.ensure_guild_admin(
        session, user=user, guild=guild, is_discord_guild_admin=ctx.is_discord_guild_admin
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/share/request",
    response_model=FeedItemResponse,
    summary="Request to share a tournament to the central feed (guild_admin; service-auth)",
)
async def request_share(body: ShareRequest) -> FeedItemResponse:
    """A source-guild admin opts a tournament into the central feed (pending).

    Resolves the source guild + admin gating (delegated to ``app.guilds.service``),
    then DELEGATES to :func:`app.federation.service.request_share` which enforces the
    per-guild ``central_feed_enabled`` consent gate and builds the whitelisted
    projection (PLAN.md §3.2/§9).
    """
    ctx = body.ctx.to_ctx()
    async with SessionLocal() as session, session.begin():
        try:
            user = await guilds_service.upsert_user_by_discord_id(
                session,
                discord_user_id=ctx.discord_user_id,
                display_name=ctx.display_name,
            )
            guild = await guilds_service.resolve_guild_by_discord_id(
                session, discord_guild_id=ctx.discord_guild_id
            )
            await guilds_service.set_tenant_guild(session, guild_id=guild.id)
            await guilds_service.ensure_guild_admin(
                session,
                user=user,
                guild=guild,
                is_discord_guild_admin=ctx.is_discord_guild_admin,
            )
            tournament = await federation.get_tournament_for_share(
                session, tournament_id=body.tournament_id
            )
            view = await federation.request_share(
                session,
                tournament=tournament,
                source_guild=guild,
                requested_by=user.id,
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except guilds_service.NotGuildAdminError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="guild admin required"
            ) from exc
        except federation.FeedNotEnabledError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="central feed not enabled for this guild",
            ) from exc
        except federation.FeedItemNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="tournament not found"
            ) from exc
    return FeedItemResponse.of(view)


@router.post(
    "/share/approve",
    response_model=FeedItemResponse,
    summary="Approve a pending central-feed item → published (central staff; service-auth)",
)
async def approve_share(body: FeedItemRefRequest) -> FeedItemResponse:
    """Central staff approves a pending item → published (PLAN.md §3.2/§784)."""
    ctx = body.ctx.to_ctx()
    async with SessionLocal() as session, session.begin():
        try:
            user = await guilds_service.upsert_user_by_discord_id(
                session,
                discord_user_id=ctx.discord_user_id,
                display_name=ctx.display_name,
            )
            await _require_central_admin(session, ctx)
            view = await federation.approve_share(
                session,
                item_id=body.item_id,
                approved_by=user.id,
                is_platform_operator=True,
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except (guilds_service.NotGuildAdminError, federation.NotCentralStaffError) as exc:
            raise _not_central_staff(exc) from exc
        except federation.AlreadyApprovedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="item already published"
            ) from exc
        except federation.FeedItemNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="feed item not found"
            ) from exc
    return FeedItemResponse.of(view)


@router.post(
    "/share/revoke",
    response_model=FeedItemResponse,
    summary="Revoke a central-feed item (central staff; service-auth)",
)
async def revoke_share(body: FeedItemRefRequest) -> FeedItemResponse:
    """Central staff revokes an item → hidden from the feed (PLAN.md §3.2)."""
    ctx = body.ctx.to_ctx()
    async with SessionLocal() as session, session.begin():
        try:
            await _require_central_admin(session, ctx)
            view = await federation.revoke_share(
                session, item_id=body.item_id, is_platform_operator=True
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except (guilds_service.NotGuildAdminError, federation.NotCentralStaffError) as exc:
            raise _not_central_staff(exc) from exc
        except federation.FeedItemNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="feed item not found"
            ) from exc
    return FeedItemResponse.of(view)


@router.get(
    "/feed",
    response_model=list[FeedItemResponse],
    summary="List the published, non-revoked central feed (service-auth)",
)
async def list_feed() -> list[FeedItemResponse]:
    """Return published, non-revoked central-feed items (projected rows only).

    The feed reads EXCLUSIVELY from ``central_feed_items`` — there is no live
    cross-tenant query into any guild's tournament data (PLAN.md §9). Runs on a plain
    session (the central feed is global by design; the items are pre-projected).
    """
    async with SessionLocal() as session, session.begin():
        views = await federation.list_central_feed(session)
    return [FeedItemResponse.of(v) for v in views]


__all__ = ["router"]
