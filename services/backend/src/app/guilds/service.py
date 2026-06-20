"""Guild onboarding/upsert + per-guild settings service (PLAN.md §3.2).

This is the data core behind the bot bridge (:mod:`app.integrations.discord`). It
is deliberately a set of plain ``async`` functions over an
:class:`~sqlalchemy.ext.asyncio.AsyncSession` so it is unit-testable against a fake
session and reused from both the bridge router and (later) lifecycle jobs.

What it owns
------------
* :func:`upsert_user_by_discord_id` — resolve/create the **global**
  :class:`~app.models.User` for a Discord user snowflake (the bot only knows the
  snowflake; the backend maps it to our UUID identity).
* :func:`onboard_guild` — upsert the :class:`~app.models.Guild` anchor for a
  Discord guild snowflake on ``GUILD_JOIN``, applying per-guild settings, and seed
  the inviting admin a ``guild_admin`` ``user_guild_roles`` row.
* :func:`resolve_guild_by_discord_id` / :func:`set_tenant_guild` — resolve an
  ONBOARDED guild and set the ``app.current_guild`` GUC so subsequent guild-scoped
  queries (delegated to tournaments/teams) run under RLS for that tenant.
* :func:`ensure_guild_admin` — gate a guild-admin action on the asserted
  Manage-Guild permission OR a persisted ``guild_admin`` role, seeding the role row
  on first admin action so the membership becomes durable.
* :func:`get_settings` / :func:`update_settings` — read/merge the typed
  :class:`~app.guilds.settings.GuildSettings` on ``guilds.settings``.

RLS / tenancy note (PLAN.md §9)
-------------------------------
The bridge has NO user JWT, so :func:`onboard_guild` resolves the guild by Discord
snowflake on the GLOBAL ``guilds`` table (not guild-RLS-scoped) and writes the
``user_guild_roles`` row under an explicitly-set GUC. Delegated tournament/team
calls run AFTER :func:`set_tenant_guild` has set ``app.current_guild`` for the
resolved guild, so their inserts derive ``guild_id`` from the GUC exactly as on the
JWT request path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from lol_schemas.enums import GuildStatus, UserGuildRoleName
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.guilds.settings import GuildSettings, GuildSettingsPatch
from app.logging import get_logger
from app.models import Guild, User, UserGuildRole

log = get_logger("app.guilds")

GUILD_ADMIN = UserGuildRoleName.GUILD_ADMIN


class GuildError(Exception):
    """Base class for guild onboarding/settings failures."""


class GuildNotOnboardedError(GuildError):
    """No onboarded guild exists for the supplied Discord snowflake (404/409)."""


class NotGuildAdminError(GuildError):
    """The acting user is not a guild admin (Manage-Guild + no guild_admin row)."""


@dataclass(frozen=True, slots=True)
class OnboardResult:
    """Outcome of an onboard/upsert (PLAN.md §3.2 GUILD_JOIN)."""

    guild_id: str
    discord_guild_id: int
    created: bool
    admin_seeded: bool
    settings: GuildSettings


# ---------------------------------------------------------------------------
# Global identity resolution (Discord snowflake -> our UUID)
# ---------------------------------------------------------------------------
async def upsert_user_by_discord_id(
    session: AsyncSession,
    *,
    discord_user_id: int,
    display_name: str | None = None,
) -> User:
    """Resolve (or create) the global :class:`User` for a Discord user snowflake.

    ``users`` is global (not guild-scoped), so this runs irrespective of the
    tenant GUC. The display name is refreshed when supplied (non-identity display
    data, PLAN.md §2). PUUID/identity is unaffected — this only maps the Discord
    snowflake the bot asserts to our UUID identity.
    """
    user = (
        await session.execute(select(User).where(User.discord_user_id == discord_user_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            discord_user_id=discord_user_id,
            display_name=display_name or str(discord_user_id),
        )
        session.add(user)
        await session.flush()
        return user
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    return user


# ---------------------------------------------------------------------------
# Guild resolution + tenant GUC
# ---------------------------------------------------------------------------
async def resolve_guild_by_discord_id(session: AsyncSession, *, discord_guild_id: int) -> Guild:
    """Load an ONBOARDED guild by its Discord snowflake.

    ``guilds`` is global, so this does not require a tenant GUC. A removed/never-
    onboarded guild raises :class:`GuildNotOnboardedError` (the bridge maps it to
    409: the bot must be (re-)invited / the guild synced first, PLAN.md §3.2).
    """
    guild = (
        await session.execute(select(Guild).where(Guild.discord_guild_id == discord_guild_id))
    ).scalar_one_or_none()
    if guild is None or guild.status == GuildStatus.REMOVED:
        raise GuildNotOnboardedError(str(discord_guild_id))
    return guild


async def set_tenant_guild(session: AsyncSession, *, guild_id: uuid.UUID | str) -> None:
    """Set ``app.current_guild`` for the session transaction (RLS scope).

    Mirrors :func:`app.auth.deps.get_tenant_session`'s GUC set so delegated
    tournament/team inserts derive ``guild_id`` from the GUC, never from request
    input (PLAN.md §9). ``SET LOCAL`` semantics: scoped to the current transaction.
    """
    await session.execute(
        text("SELECT set_config('app.current_guild', :gid, true)"),
        {"gid": str(guild_id)},
    )


# ---------------------------------------------------------------------------
# Onboarding (GUILD_JOIN upsert) + admin gating
# ---------------------------------------------------------------------------
async def _seed_guild_admin(
    session: AsyncSession, *, user_id: uuid.UUID, guild_id: uuid.UUID
) -> bool:
    """Insert a ``guild_admin`` ``user_guild_roles`` row if absent (idempotent).

    Returns ``True`` when a row was newly created. The lookup/insert run under the
    tenant GUC (set by :func:`set_tenant_guild`), so RLS scopes them to this guild.
    The unique ``(user_id, guild_id, role)`` constraint makes the insert idempotent
    even under a race; the pre-check just turns it into a clean no-op.
    """
    existing = (
        await session.execute(
            select(UserGuildRole.id).where(
                UserGuildRole.user_id == user_id,
                UserGuildRole.guild_id == guild_id,
                UserGuildRole.role == GUILD_ADMIN,
            )
        )
    ).first()
    if existing is not None:
        return False
    session.add(UserGuildRole(user_id=user_id, guild_id=guild_id, role=GUILD_ADMIN))
    await session.flush()
    return True


async def onboard_guild(
    session: AsyncSession,
    *,
    discord_guild_id: int,
    name: str,
    admin_discord_user_id: int,
    admin_display_name: str | None = None,
    settings: GuildSettingsPatch | None = None,
) -> OnboardResult:
    """Upsert the guild anchor on ``GUILD_JOIN`` and seed the inviting admin.

    Flow (PLAN.md §3.2 multi-tenant bot, "any guild can invite it"):

    1. Upsert the global :class:`User` for the inviting/admin Discord snowflake.
    2. Upsert the :class:`Guild` anchor for the Discord snowflake — create it
       ``active`` on first join, or re-activate + refresh ``name``/``settings`` on a
       re-sync (a previously ``removed`` guild is reactivated within retention).
    3. Apply the optional settings patch onto the typed
       :class:`~app.guilds.settings.GuildSettings` blob.
    4. Set the tenant GUC and seed the admin a ``guild_admin`` role row (so the
       inviting user can immediately run admin actions — PLAN.md §3.2 onboarding).

    Idempotent: re-syncing an already-onboarded guild updates display/settings and
    re-seeds the admin role only if missing.
    """
    admin_user = await upsert_user_by_discord_id(
        session,
        discord_user_id=admin_discord_user_id,
        display_name=admin_display_name,
    )

    guild = (
        await session.execute(select(Guild).where(Guild.discord_guild_id == discord_guild_id))
    ).scalar_one_or_none()
    created = guild is None
    if guild is None:
        guild = Guild(
            discord_guild_id=discord_guild_id,
            name=name,
            status=GuildStatus.ACTIVE,
            settings={},
        )
        session.add(guild)
        await session.flush()
    else:
        guild.name = name
        # Re-invite within retention reactivates the soft-removed anchor.
        guild.status = GuildStatus.ACTIVE
        guild.removed_at = None

    current = GuildSettings.from_jsonb(guild.settings)
    if settings is not None:
        current = current.merge(settings)
    guild.settings = current.to_jsonb()

    await set_tenant_guild(session, guild_id=guild.id)
    admin_seeded = await _seed_guild_admin(session, user_id=admin_user.id, guild_id=guild.id)

    log.info(
        "guild_onboarded",
        guild_id=str(guild.id),
        discord_guild_id=discord_guild_id,
        created=created,
        admin_seeded=admin_seeded,
    )
    return OnboardResult(
        guild_id=str(guild.id),
        discord_guild_id=discord_guild_id,
        created=created,
        admin_seeded=admin_seeded,
        settings=current,
    )


async def ensure_guild_admin(
    session: AsyncSession,
    *,
    user: User,
    guild: Guild,
    is_discord_guild_admin: bool,
) -> None:
    """Gate a guild-admin action (request-scoped; no durable role seeding).

    The caller must hold either the asserted Discord Manage-Guild permission
    (``is_discord_guild_admin``, trusted because only the bot can present the
    service token) OR a persisted ``guild_admin`` ``user_guild_roles`` row. A user
    with neither raises :class:`NotGuildAdminError`.

    SECURITY: the Manage-Guild assertion authorizes THIS request only — it does
    NOT durably seed a ``guild_admin`` row. Seeding a permanent role from a
    transient, bot-asserted boolean would turn a single forged/compromised-token
    call into a persistent admin backdoor that survives the Discord permission
    being revoked. Manage-Guild is the live source of truth and is re-asserted
    every request; durable role rows are seeded only at onboarding (the guild
    owner) or an explicit grant flow.

    Requires the tenant GUC to already be set for ``guild`` (RLS scopes the role
    lookup) — the bridge calls :func:`set_tenant_guild` first.
    """
    if is_discord_guild_admin:
        return  # authorize this request only; do NOT persist a durable admin row.

    has_role = (
        await session.execute(
            select(UserGuildRole.id).where(
                UserGuildRole.user_id == user.id,
                UserGuildRole.guild_id == guild.id,
                UserGuildRole.role == GUILD_ADMIN,
            )
        )
    ).first()
    if has_role is None:
        raise NotGuildAdminError(str(user.id))


# ---------------------------------------------------------------------------
# Per-guild settings
# ---------------------------------------------------------------------------
def get_settings(guild: Guild) -> GuildSettings:
    """Return the typed settings view over ``guild.settings`` (PLAN.md §3.2)."""
    return GuildSettings.from_jsonb(guild.settings)


async def update_settings(
    session: AsyncSession, *, guild: Guild, patch: GuildSettingsPatch
) -> GuildSettings:
    """Merge ``patch`` into ``guild.settings`` and persist (PATCH semantics).

    Only the explicitly-set patch fields override the current settings; the merged
    blob is written back to the JSONB column. Runs in the caller's transaction.
    """
    merged = GuildSettings.from_jsonb(guild.settings).merge(patch)
    guild.settings = merged.to_jsonb()
    await session.flush()
    return merged


__all__ = [
    "GUILD_ADMIN",
    "GuildError",
    "GuildNotOnboardedError",
    "NotGuildAdminError",
    "OnboardResult",
    "ensure_guild_admin",
    "get_settings",
    "onboard_guild",
    "resolve_guild_by_discord_id",
    "set_tenant_guild",
    "update_settings",
    "upsert_user_by_discord_id",
]
