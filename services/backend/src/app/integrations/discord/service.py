"""Discord bridge service: resolve identity/tenant, gate, DELEGATE (PLAN.md §3.2).

This is the orchestration core behind :mod:`app.integrations.discord.router`. Every
bridge operation follows the same shape and adds **no** domain logic of its own:

1. Resolve the acting :class:`~app.models.User` from the asserted
   ``discord_user_id`` (upsert — the bot only knows the snowflake).
2. Resolve the onboarded :class:`~app.models.Guild` from ``discord_guild_id``
   (409 if the guild was never onboarded / was removed).
3. Set ``app.current_guild`` so the delegated call runs under RLS for that tenant.
4. For admin actions, gate on the asserted Manage-Guild permission OR a persisted
   ``guild_admin`` role (seeding the role on first admin action).
5. **Delegate** to the existing ``app.tournaments`` / ``app.teams`` service —
   never re-implement registration caps, captain guards, bracket planning, etc.

The functions are plain ``async`` callables over an
:class:`~sqlalchemy.ext.asyncio.AsyncSession` so they are unit-testable with a fake
session (see ``tests/test_discord_bridge.py``); the router owns the HTTP shell and
the session transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.guilds import service as guilds_service
from app.guilds.settings import GuildSettings, GuildSettingsPatch
from app.models import Guild, Registration, Team, TeamMember, Tournament, User
from app.teams import service as teams_service
from app.tournaments import service as tournaments_service


@dataclass(frozen=True, slots=True)
class DiscordContext:
    """The Discord interaction context the bot ASSERTS on each bridge call.

    Trusted because only the bot holds the service token (see
    :mod:`app.integrations.discord.service_auth`). ``is_discord_guild_admin`` is the
    invoking member's Manage-Guild permission as seen by the bot.
    """

    discord_user_id: int
    discord_guild_id: int
    is_discord_guild_admin: bool
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedActor:
    """The acting user + onboarded guild for a bridge call (GUC already set)."""

    user: User
    guild: Guild


async def _resolve_actor(session: AsyncSession, ctx: DiscordContext) -> ResolvedActor:
    """Resolve user + onboarded guild and set the tenant GUC (steps 1–3).

    Raises:
        guilds_service.GuildNotOnboardedError: the guild was never onboarded.
    """
    user = await guilds_service.upsert_user_by_discord_id(
        session,
        discord_user_id=ctx.discord_user_id,
        display_name=ctx.display_name,
    )
    guild = await guilds_service.resolve_guild_by_discord_id(
        session, discord_guild_id=ctx.discord_guild_id
    )
    await guilds_service.set_tenant_guild(session, guild_id=guild.id)
    return ResolvedActor(user=user, guild=guild)


async def _resolve_actor_admin(session: AsyncSession, ctx: DiscordContext) -> ResolvedActor:
    """As :func:`_resolve_actor`, then enforce guild-admin gating (step 4).

    Raises:
        guilds_service.NotGuildAdminError: not Manage-Guild and no guild_admin row.
    """
    actor = await _resolve_actor(session, ctx)
    await guilds_service.ensure_guild_admin(
        session,
        user=actor.user,
        guild=actor.guild,
        is_discord_guild_admin=ctx.is_discord_guild_admin,
    )
    return actor


# ---------------------------------------------------------------------------
# Onboarding / settings (PLAN.md §3.2 GUILD_JOIN + per-guild settings)
# ---------------------------------------------------------------------------
async def onboard_guild(
    session: AsyncSession,
    *,
    ctx: DiscordContext,
    name: str,
    settings: GuildSettingsPatch | None = None,
) -> guilds_service.OnboardResult:
    """Upsert the guild on ``GUILD_JOIN`` and seed the inviting admin (delegated).

    Onboarding does NOT require a pre-existing guild (it creates one), so it does
    not go through :func:`_resolve_actor`; it delegates straight to
    :func:`app.guilds.service.onboard_guild`, which upserts the user + anchor, sets
    the GUC, and seeds the ``guild_admin`` role.
    """
    return await guilds_service.onboard_guild(
        session,
        discord_guild_id=ctx.discord_guild_id,
        name=name,
        admin_discord_user_id=ctx.discord_user_id,
        admin_display_name=ctx.display_name,
        settings=settings,
    )


async def get_guild_settings(session: AsyncSession, *, ctx: DiscordContext) -> GuildSettings:
    """Read the per-guild settings for the acting guild (any member)."""
    actor = await _resolve_actor(session, ctx)
    return guilds_service.get_settings(actor.guild)


async def resolve_guild_view(
    session: AsyncSession, *, ctx: DiscordContext
) -> tuple[str, GuildSettings]:
    """Read-only resolve of a Discord guild to (backend ``guild_id``, settings).

    Used by the bot's notification router to map a backend ``origin_guild_id`` to
    the Discord guild + ``notify_channel_id`` WITHOUT the mutating onboard upsert
    (no guild re-activation, no admin re-seeding on the notification hot path).
    """
    actor = await _resolve_actor(session, ctx)
    return str(actor.guild.id), guilds_service.get_settings(actor.guild)


async def update_guild_settings(
    session: AsyncSession, *, ctx: DiscordContext, patch: GuildSettingsPatch
) -> GuildSettings:
    """Merge a settings patch for the acting guild (guild_admin only)."""
    actor = await _resolve_actor_admin(session, ctx)
    return await guilds_service.update_settings(session, guild=actor.guild, patch=patch)


# ---------------------------------------------------------------------------
# Teams (delegated to app.teams.service)
# ---------------------------------------------------------------------------
async def create_team(session: AsyncSession, *, ctx: DiscordContext, name: str) -> Team:
    """Create a team captained by the acting user (delegated)."""
    actor = await _resolve_actor(session, ctx)
    return await teams_service.create_team(session, name=name, captain_user_id=actor.user.id)


async def join_team(
    session: AsyncSession, *, ctx: DiscordContext, team_id: uuid.UUID
) -> TeamMember:
    """The acting user joins ``team_id`` as a player (delegated add_member)."""
    actor = await _resolve_actor(session, ctx)
    return await teams_service.add_member(session, team_id=team_id, user_id=actor.user.id)


async def leave_team(session: AsyncSession, *, ctx: DiscordContext, team_id: uuid.UUID) -> None:
    """The acting user leaves ``team_id`` (delegated; captain guard applies)."""
    actor = await _resolve_actor(session, ctx)
    await teams_service.leave_team(session, team_id=team_id, user_id=actor.user.id)


# ---------------------------------------------------------------------------
# Tournaments (delegated to app.tournaments.service)
# ---------------------------------------------------------------------------
async def list_tournaments(session: AsyncSession, *, ctx: DiscordContext) -> list[Tournament]:
    """List the acting guild's tournaments (delegated, RLS-scoped)."""
    await _resolve_actor(session, ctx)
    return list(await tournaments_service.list_tournaments(session))


async def create_tournament(
    session: AsyncSession,
    *,
    ctx: DiscordContext,
    name: str,
    max_teams: int = tournaments_service.MAX_TEAMS_HARD_CAP,
) -> Tournament:
    """Create a tournament in the acting guild (guild_admin; delegated)."""
    await _resolve_actor_admin(session, ctx)
    return await tournaments_service.create_tournament(session, name=name, max_teams=max_teams)


async def join_tournament(
    session: AsyncSession,
    *,
    ctx: DiscordContext,
    tournament_id: uuid.UUID,
    team_id: uuid.UUID,
) -> Registration:
    """Register ``team_id`` into ``tournament_id`` (delegated register_team).

    The happy path the bot's ``/join`` command drives: resolve user+guild, set the
    GUC, then delegate to the tournament service's ``register_team`` (which enforces
    the max-teams cap and registration-open state). No logic is duplicated here.
    """
    await _resolve_actor(session, ctx)
    return await tournaments_service.register_team(
        session, tournament_id=tournament_id, team_id=team_id
    )


async def leave_tournament(
    session: AsyncSession,
    *,
    ctx: DiscordContext,
    tournament_id: uuid.UUID,
    team_id: uuid.UUID,
) -> None:
    """Withdraw ``team_id`` from ``tournament_id`` (delegated).

    Delegates to the tournament service's withdrawal helper. Until that helper
    exists in ``app.tournaments.service`` this raises so the gap is explicit rather
    than re-implementing registration deletion here (no duplicated logic).
    """
    await _resolve_actor(session, ctx)
    withdraw = getattr(tournaments_service, "withdraw_team", None)
    if withdraw is None:  # pragma: no cover - delegated helper not yet present
        raise NotImplementedError(
            "app.tournaments.service.withdraw_team is not implemented; the bridge "
            "delegates rather than duplicating registration-removal logic"
        )
    await withdraw(session, tournament_id=tournament_id, team_id=team_id)


async def tournament_status(
    session: AsyncSession, *, ctx: DiscordContext, tournament_id: uuid.UUID
) -> Tournament:
    """Fetch a single tournament's status for the acting guild (delegated)."""
    await _resolve_actor(session, ctx)
    return await tournaments_service.get_tournament(session, tournament_id=tournament_id)


__all__ = [
    "DiscordContext",
    "ResolvedActor",
    "create_team",
    "create_tournament",
    "get_guild_settings",
    "join_team",
    "join_tournament",
    "leave_team",
    "leave_tournament",
    "list_tournaments",
    "onboard_guild",
    "tournament_status",
    "update_guild_settings",
]
