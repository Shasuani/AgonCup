"""Discord bot bridge router (PLAN.md §3.2 multi-tenant bot).

Service-authenticated HTTP surface the Discord bot calls (it holds the only copy
of ``bot_service_token``). Every route:

* is gated by :func:`app.integrations.discord.service_auth.require_service_token`
  (401/503 — fail closed);
* receives the Discord context the bot ASSERTS (``discord_user_id``,
  ``discord_guild_id``, ``is_discord_guild_admin``) embedded in the request body;
* opens a single tenant transaction, delegates to
  :mod:`app.integrations.discord.service` (which resolves identity/tenant, sets the
  RLS GUC, gates admin, and forwards to the tournaments/teams/accounts services),
  and maps domain errors to HTTP.

NO domain logic lives here or in the bridge service — registration caps, captain
guards, bracket planning, Account-V1 resolution all stay in their owning services.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from lol_riot import RiotAccountClient
from pydantic import BaseModel, Field

from app.accounts import service as accounts_service
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.guilds import service as guilds_service
from app.guilds.settings import GuildSettings, GuildSettingsPatch
from app.integrations.discord import service as bridge
from app.integrations.discord.service import DiscordContext
from app.integrations.discord.service_auth import require_service_token
from app.logging import get_logger
from app.models import Registration, Team, Tournament
from app.teams import service as teams_service
from app.tournaments import service as tournaments_service

router = APIRouter(
    prefix="/integrations/discord",
    tags=["integrations:discord"],
    dependencies=[Depends(require_service_token)],
)
log = get_logger("app.integrations.discord")


# ---------------------------------------------------------------------------
# Shared request context (the Discord interaction the bot asserts)
# ---------------------------------------------------------------------------
class DiscordContextIn(BaseModel):
    """The asserted Discord interaction context, embedded in every bridge call.

    Trusted because only the bot holds the service token (PLAN.md §3.2). See
    :mod:`app.integrations.discord.service_auth` for the boundary rationale.
    """

    discord_user_id: int = Field(..., description="Invoking member's snowflake.")
    discord_guild_id: int = Field(..., description="Guild the command ran in.")
    is_discord_guild_admin: bool = Field(
        default=False,
        description="The member's Manage-Guild permission, as seen by the bot.",
    )
    display_name: str | None = Field(
        default=None, description="Discord display name (refreshable, non-identity)."
    )

    def to_ctx(self) -> DiscordContext:
        return DiscordContext(
            discord_user_id=self.discord_user_id,
            discord_guild_id=self.discord_guild_id,
            is_discord_guild_admin=self.is_discord_guild_admin,
            display_name=self.display_name,
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class GuildSettingsModel(BaseModel):
    admin_role_ids: list[int]
    notify_channel_id: int | None
    central_feed_enabled: bool

    @classmethod
    def of(cls, s: GuildSettings) -> GuildSettingsModel:
        return cls(
            admin_role_ids=s.admin_role_ids,
            notify_channel_id=s.notify_channel_id,
            central_feed_enabled=s.central_feed_enabled,
        )


class OnboardRequest(BaseModel):
    ctx: DiscordContextIn
    name: str = Field(min_length=1, max_length=120, description="Discord guild name.")
    settings: GuildSettingsPatch | None = None


class OnboardResponse(BaseModel):
    guild_id: str
    discord_guild_id: int
    created: bool
    admin_seeded: bool
    settings: GuildSettingsModel


class SettingsUpdateRequest(BaseModel):
    ctx: DiscordContextIn
    patch: GuildSettingsPatch


class ContextRequest(BaseModel):
    """A call carrying only the interaction context (no extra payload)."""

    ctx: DiscordContextIn


class LinkAccountRequest(BaseModel):
    ctx: DiscordContextIn
    game_name: str = Field(min_length=1, max_length=64, alias="gameName")
    tag_line: str = Field(min_length=1, max_length=16, alias="tagLine")
    region: str = Field(min_length=2, max_length=8)

    model_config = {"populate_by_name": True}


class LinkAccountResponse(BaseModel):
    account_id: str
    puuid: str
    verified: bool
    attempt_id: str
    target_icon_id: int
    game_name: str
    tag_line: str


class TeamCreateRequest(BaseModel):
    ctx: DiscordContextIn
    name: str = Field(min_length=1, max_length=120)


class TeamRefRequest(BaseModel):
    ctx: DiscordContextIn
    team_id: uuid.UUID


class TeamResponse(BaseModel):
    id: str
    name: str
    captain_user_id: str
    guild_id: str


class TournamentCreateRequest(BaseModel):
    ctx: DiscordContextIn
    name: str = Field(min_length=1, max_length=120)
    max_teams: int = Field(default=16, ge=2, le=16)


class TournamentJoinRequest(BaseModel):
    ctx: DiscordContextIn
    tournament_id: uuid.UUID
    team_id: uuid.UUID


class TournamentRefRequest(BaseModel):
    ctx: DiscordContextIn
    tournament_id: uuid.UUID


class TournamentResponse(BaseModel):
    id: str
    name: str
    status: str
    format: str
    max_teams: int
    is_shared_to_central: bool


class RegistrationResponse(BaseModel):
    id: str
    tournament_id: str
    team_id: str
    status: str


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _tournament_response(t: Tournament) -> TournamentResponse:
    return TournamentResponse(
        id=str(t.id),
        name=t.name,
        status=_enum_value(t.status),
        format=_enum_value(t.format),
        max_teams=t.max_teams,
        is_shared_to_central=t.is_shared_to_central,
    )


def _team_response(t: Team) -> TeamResponse:
    return TeamResponse(
        id=str(t.id),
        name=t.name,
        captain_user_id=str(t.captain_user_id),
        guild_id=str(t.guild_id),
    )


def _registration_response(reg: Registration) -> RegistrationResponse:
    return RegistrationResponse(
        id=str(reg.id),
        tournament_id=str(reg.tournament_id),
        team_id=str(reg.team_id),
        status=_enum_value(reg.status),
    )


# ---------------------------------------------------------------------------
# Error mapping (domain → HTTP). Keeps each route a thin try/except shell.
# ---------------------------------------------------------------------------
def _guild_not_onboarded(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="guild is not onboarded; invite/sync the bot first",
    )


def _not_admin(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild admin required")


# ---------------------------------------------------------------------------
# Onboarding / settings
# ---------------------------------------------------------------------------
@router.post(
    "/guilds/onboard",
    response_model=OnboardResponse,
    summary="Upsert a guild on GUILD_JOIN + seed the inviting admin (service-auth)",
)
async def onboard_guild(body: OnboardRequest) -> OnboardResponse:
    """Onboard/sync a guild and seed the inviting admin a guild_admin role."""
    async with SessionLocal() as session, session.begin():
        result = await bridge.onboard_guild(
            session, ctx=body.ctx.to_ctx(), name=body.name, settings=body.settings
        )
    return OnboardResponse(
        guild_id=result.guild_id,
        discord_guild_id=result.discord_guild_id,
        created=result.created,
        admin_seeded=result.admin_seeded,
        settings=GuildSettingsModel.of(result.settings),
    )


@router.post(
    "/guilds/settings/get",
    response_model=GuildSettingsModel,
    summary="Read per-guild bot settings (service-auth)",
)
async def read_guild_settings(body: ContextRequest) -> GuildSettingsModel:
    async with SessionLocal() as session, session.begin():
        try:
            settings = await bridge.get_guild_settings(session, ctx=body.ctx.to_ctx())
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
    return GuildSettingsModel.of(settings)


class GuildResolveModel(BaseModel):
    """Read-only id↔id + settings view for notification routing."""

    guild_id: str
    discord_guild_id: int
    settings: GuildSettingsModel


@router.post(
    "/guilds/resolve",
    response_model=GuildResolveModel,
    summary="Read-only resolve Discord guild -> backend guild_id + settings (service-auth)",
)
async def resolve_guild(body: ContextRequest) -> GuildResolveModel:
    # Read-only: used by the bot's notification router to map origin_guild_id to a
    # Discord guild/channel without the mutating onboard upsert (no admin re-seed).
    async with SessionLocal() as session, session.begin():
        try:
            guild_id, settings = await bridge.resolve_guild_view(session, ctx=body.ctx.to_ctx())
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
    return GuildResolveModel(
        guild_id=guild_id,
        discord_guild_id=body.ctx.discord_guild_id,
        settings=GuildSettingsModel.of(settings),
    )


@router.post(
    "/guilds/settings/update",
    response_model=GuildSettingsModel,
    summary="Update per-guild bot settings (guild_admin; service-auth)",
)
async def update_settings(body: SettingsUpdateRequest) -> GuildSettingsModel:
    async with SessionLocal() as session, session.begin():
        try:
            settings = await bridge.update_guild_settings(
                session, ctx=body.ctx.to_ctx(), patch=body.patch
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except guilds_service.NotGuildAdminError as exc:
            raise _not_admin(exc) from exc
    return GuildSettingsModel.of(settings)


# ---------------------------------------------------------------------------
# Account linking (delegated to app.accounts.service; Riot client built here)
# ---------------------------------------------------------------------------
@router.post(
    "/accounts/link",
    response_model=LinkAccountResponse,
    summary="Start Riot account verification for the acting user (service-auth)",
)
async def link_account(
    body: LinkAccountRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LinkAccountResponse:
    """Resolve the acting user, then DELEGATE to the accounts service.

    Account linking is GLOBAL (not guild-scoped): the user is resolved by Discord
    snowflake, but the link itself uses a plain session (no tenant GUC), mirroring
    ``app.accounts.router``. The Riot client is constructed here exactly as in that
    router; the bridge adds no Account-V1 logic of its own.
    """
    try:
        platform = accounts_service.parse_platform_region(body.region)
    except accounts_service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    client = RiotAccountClient(settings.riot_api_key, accounts_service.continent_for(platform))
    try:
        async with SessionLocal() as session, session.begin():
            user = await guilds_service.upsert_user_by_discord_id(
                session,
                discord_user_id=body.ctx.discord_user_id,
                display_name=body.ctx.display_name,
            )
            result = await accounts_service.link_account(
                session,
                client,
                user_id=str(user.id),
                game_name=body.game_name,
                tag_line=body.tag_line,
                region=body.region,
            )
    except accounts_service.UnknownRiotId as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except accounts_service.RiotUnavailable as exc:
        log.warning("account_v1_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Riot Account-V1 unavailable; try again",
        ) from exc
    finally:
        await client.aclose()

    return LinkAccountResponse(
        account_id=result.account_id,
        puuid=result.puuid,
        verified=result.verified,
        attempt_id=result.attempt_id,
        target_icon_id=result.target_icon_id,
        game_name=result.game_name,
        tag_line=result.tag_line,
    )


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
@router.post("/teams/create", response_model=TeamResponse, summary="Create a team (service-auth)")
async def create_team(body: TeamCreateRequest) -> TeamResponse:
    async with SessionLocal() as session, session.begin():
        try:
            team = await bridge.create_team(session, ctx=body.ctx.to_ctx(), name=body.name)
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
    return _team_response(team)


@router.post(
    "/teams/join", response_model=TeamResponse, summary="Join a team as a player (service-auth)"
)
async def join_team(body: TeamRefRequest) -> TeamResponse:
    async with SessionLocal() as session, session.begin():
        try:
            await bridge.join_team(session, ctx=body.ctx.to_ctx(), team_id=body.team_id)
            team = await teams_service.get_team(session, team_id=body.team_id)
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except teams_service.TeamNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
        except teams_service.DuplicateMemberError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="already on team") from exc
        except teams_service.InvalidRoleError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _team_response(team)


@router.post(
    "/teams/leave", status_code=status.HTTP_204_NO_CONTENT, summary="Leave a team (service-auth)"
)
async def leave_team(body: TeamRefRequest) -> None:
    async with SessionLocal() as session, session.begin():
        try:
            await bridge.leave_team(session, ctx=body.ctx.to_ctx(), team_id=body.team_id)
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except teams_service.TeamNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
        except teams_service.MemberNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found") from exc
        except teams_service.CaptainProtectedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------
@router.post(
    "/tournaments/list",
    response_model=list[TournamentResponse],
    summary="List the guild's tournaments (service-auth)",
)
async def list_tournaments(body: ContextRequest) -> list[TournamentResponse]:
    async with SessionLocal() as session, session.begin():
        try:
            rows = await bridge.list_tournaments(session, ctx=body.ctx.to_ctx())
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
    return [_tournament_response(t) for t in rows]


@router.post(
    "/tournaments/create",
    response_model=TournamentResponse,
    summary="Create a tournament (guild_admin; service-auth)",
)
async def create_tournament(body: TournamentCreateRequest) -> TournamentResponse:
    async with SessionLocal() as session, session.begin():
        try:
            t = await bridge.create_tournament(
                session, ctx=body.ctx.to_ctx(), name=body.name, max_teams=body.max_teams
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except guilds_service.NotGuildAdminError as exc:
            raise _not_admin(exc) from exc
        except tournaments_service.InvalidStateError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _tournament_response(t)


@router.post(
    "/tournaments/join",
    response_model=RegistrationResponse,
    summary="Register a team into a tournament (service-auth)",
)
async def join_tournament(body: TournamentJoinRequest) -> RegistrationResponse:
    async with SessionLocal() as session, session.begin():
        try:
            reg = await bridge.join_tournament(
                session,
                ctx=body.ctx.to_ctx(),
                tournament_id=body.tournament_id,
                team_id=body.team_id,
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except tournaments_service.TournamentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
        except tournaments_service.TournamentFullError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except (
            tournaments_service.RegistrationClosedError,
            tournaments_service.InvalidStateError,
        ) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _registration_response(reg)


@router.post(
    "/tournaments/status",
    response_model=TournamentResponse,
    summary="Get a tournament's status (service-auth)",
)
async def tournament_status(body: TournamentRefRequest) -> TournamentResponse:
    async with SessionLocal() as session, session.begin():
        try:
            t = await bridge.tournament_status(
                session, ctx=body.ctx.to_ctx(), tournament_id=body.tournament_id
            )
        except guilds_service.GuildNotOnboardedError as exc:
            raise _guild_not_onboarded(exc) from exc
        except tournaments_service.TournamentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
    return _tournament_response(t)
