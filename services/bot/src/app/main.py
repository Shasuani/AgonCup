"""Discord bot entrypoint (PLAN.md §3.2).

`discord.py` **AutoShardedBot** on the non-privileged `guilds` intent only.

The bot is a **thin presentation layer** (PLAN.md §3.2): real commands
(``/register``, ``/team``, ``/tournament``, ``/tournament-create``) live in cogs
that delegate to the backend bridge via :class:`app.backend_client.BackendClient`.
NO game logic lives here.

Phase 3 wiring:

* command cogs are added in ``setup_hook`` (offline — no gateway connect);
* ``on_guild_join`` onboards the guild via the backend bridge (upsert Guild +
  settings) and ``on_guild_remove`` purges it (lifecycle), per PLAN.md §3.2;
* a Redis pub/sub :class:`app.notifications.NotificationSubscriber` task is
  started in ``setup_hook`` and cancelled on ``close``, consuming backend
  notification envelopes and posting embeds to each guild's notify channel.

Import-safety invariant (unchanged from Phase 0): constructing the bot does NO
network I/O. The gateway connects only in :func:`run` / ``__main__``; Redis/httpx
clients are built lazily inside the started task. Importing this module in tests
stays fully offline.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from app import lifecycle
from app.backend_client import BackendClient, DiscordContext
from app.cogs import setup_cogs
from app.config import get_settings
from app.notifications import NotificationSubscriber

log = logging.getLogger("app.bot.main")


async def owner_display_name(guild: discord.Guild) -> str | None:
    """The guild owner's Discord display name, or ``None`` when it cannot be read.

    The bot runs on the ``guilds`` intent only, so the member cache normally holds
    just the bot itself; when the owner is not cached, fall back to one REST
    ``fetch_member`` (no privileged intent required). Any failure yields ``None``
    so the backend leaves the stored name untouched — this must NEVER fall back
    to ``guild.name``: that is the server's label, not a person's, and writing it
    as the owner's ``display_name`` made the owner appear as "<server name>" to
    every other player (ToDoList #7).
    """
    owner_id = guild.owner_id
    if not owner_id:
        return None
    member: Any = guild.get_member(owner_id)
    if member is None:
        try:
            member = await guild.fetch_member(owner_id)
        except Exception as exc:  # never crash the gateway (PLAN.md §3.2)
            log.debug("owner lookup failed (guild=%s): %s", guild.id, exc)
            return None
    name = getattr(member, "display_name", None)
    return name or None


async def onboard_guild(bot: commands.AutoShardedBot, guild: discord.Guild) -> bool:
    """Onboard a guild via the backend bridge on GUILD_JOIN (PLAN.md §3.2).

    Reports the guild owner as the admin actor (owners always hold Manage-Guild,
    so ``is_discord_guild_admin=True`` is faithful) under the owner's own Discord
    display name (see :func:`owner_display_name`). Errors are logged and
    swallowed — a failed onboard must never crash the gateway; the next admin
    action re-onboards (the bridge upsert is idempotent). Returns ``True`` on
    success.
    """
    backend: BackendClient = getattr(bot, "backend", None) or BackendClient()
    ctx = DiscordContext(
        discord_user_id=guild.owner_id or 0,
        discord_guild_id=guild.id,
        is_discord_guild_admin=True,
        display_name=await owner_display_name(guild),
    )
    try:
        await backend.onboard_guild(ctx, name=guild.name)
    except Exception as exc:  # never crash the gateway (PLAN.md §3.2)
        log.warning("guild onboard failed (guild=%s): %s", guild.id, exc)
        return False
    log.info("guild onboard ok (guild=%s)", guild.id)
    return True


class LeagueBot(commands.AutoShardedBot):
    """The LeagueOfLeagues bot.

    Subclassing lets ``setup_hook`` register cogs and start the notification
    subscriber without any module-level gateway side effects.
    """

    def __init__(self) -> None:
        # Only the ``guilds`` intent: enough for GUILD_JOIN / GUILD_REMOVE
        # multi-tenant onboarding + lifecycle (PLAN.md §3.2) while staying below
        # the privileged-intent verification gate (PLAN.md §9).
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)

        self.backend = BackendClient()
        self._notifications: NotificationSubscriber | None = None

    async def setup_hook(self) -> None:  # pragma: no cover - runs under the gateway
        """Register cogs + start the notification subscriber (no command sync here).

        Command sync strategy (PLAN.md §3.2): global sync is slow to propagate, so
        per-guild sync is done in ``on_ready`` for fast iteration. Cogs are added
        here so the command tree is populated before that sync.
        """
        await setup_cogs(self, backend=self.backend)
        self._notifications = self._build_notifications()
        if self._notifications is not None:
            self._notifications.start()

    def _build_notifications(self) -> NotificationSubscriber | None:
        """Construct the Redis-backed notification subscriber (lazy import).

        Returns ``None`` if Redis is unavailable so the bot still runs (commands
        work; only push notifications are degraded — never crash the gateway).
        """
        try:
            from redis.asyncio import Redis  # local import: no I/O on module import

            # Typed as Any: the redis client is an injected dependency satisfying
            # the subscriber's structural _Redis protocol at runtime.
            # socket_timeout MUST be explicit: redis-py >= 8.0 defaults it to 5s,
            # and a quiet pub/sub listen() then dies with TimeoutError at 5.01s.
            redis: Any = Redis.from_url(get_settings().redis_url, socket_timeout=None)
        except Exception:  # pragma: no cover - degraded mode
            return None
        return NotificationSubscriber(self, redis=redis, backend=self.backend)

    async def close(self) -> None:  # pragma: no cover - runs under the gateway
        """Cancel the notification task before disconnecting."""
        if self._notifications is not None:
            await self._notifications.close()
        await super().close()


def build_bot() -> commands.AutoShardedBot:
    """Construct the bot (no network I/O — safe to call in tests)."""
    bot = LeagueBot()

    @bot.tree.command(name="ping", description="Liveness check.")
    async def ping(interaction: discord.Interaction) -> None:  # pragma: no cover - needs gateway
        await interaction.response.send_message("pong", ephemeral=True)

    @bot.event
    async def on_ready() -> None:  # pragma: no cover - needs gateway
        # Reconcile guilds present at startup: a server added while the bot was
        # offline arrives as an existing guild (no on_guild_join), so onboard each
        # here too (idempotent upsert). Then sync commands per-guild for fast
        # propagation (PLAN.md §3.2 sync strategy).
        for guild in bot.guilds:
            await onboard_guild(bot, guild)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        """Joining a server onboards it (upsert Guild + settings, PLAN.md §3.2).

        Delegates to the service-authenticated backend bridge ``onboard`` (upsert
        Guild + seed the inviting admin). The guild owner is reported as the
        admin actor (``is_discord_guild_admin=True``) — the owner always holds
        Manage-Guild. Failures are logged and swallowed so the gateway loop is
        never broken; the guild is re-onboarded on the next admin action /
        notification lookup. Per-guild commands are synced too.
        """
        await onboard_guild(bot, guild)
        bot.tree.copy_global_to(guild=guild)  # pragma: no cover - needs gateway
        await bot.tree.sync(guild=guild)  # pragma: no cover - needs gateway

    @bot.event
    async def on_guild_remove(guild: discord.Guild) -> None:
        """Leaving a server purges its guild-scoped data (PLAN.md §3.2 / §7 PII).

        Delegates to the service-authenticated backend purge endpoint; failures are
        logged and swallowed there so the gateway event loop is never broken.
        """
        await lifecycle.purge_guild(guild.id)

    return bot


# Construct at import so tests can introspect the command tree without connecting.
bot = build_bot()


def run() -> None:
    """Start the gateway connection. Requires DISCORD_BOT_TOKEN."""
    settings = get_settings()
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set; cannot start the bot.")
    bot.run(settings.discord_bot_token)


def list_command_names() -> list[str]:
    """Helper for tests: the registered application-command names."""
    return [cmd.name for cmd in bot.tree.get_commands()]


if __name__ == "__main__":  # pragma: no cover
    run()


__all__ = [
    "bot",
    "build_bot",
    "run",
    "list_command_names",
    "owner_display_name",
    "app_commands",
    "lifecycle",
]
