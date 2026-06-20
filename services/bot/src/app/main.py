"""Discord bot entrypoint (PLAN.md §3.2).

`discord.py` **AutoShardedBot** on the non-privileged `guilds` intent only.

Phase 0 status
--------------
Runnable skeleton with a single ``/ping`` slash command. Constructing the bot is
side-effect-free (no network); the gateway connection only starts in
:func:`run` / ``__main__`` when ``DISCORD_BOT_TOKEN`` is set — so importing this
module in tests stays offline.

The bot is a thin presentation layer: real commands (``/register``, ``/team``,
``/tournament``) call the backend API and are added as cogs in Phase 3.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.config import get_settings


def build_bot() -> commands.AutoShardedBot:
    """Construct the bot (no network I/O — safe to call in tests).

    Uses only the ``guilds`` intent: enough to receive ``GUILD_JOIN`` for
    multi-tenant onboarding (PLAN.md §3.2) while staying below the
    privileged-intent verification gate (PLAN.md §9).
    """
    intents = discord.Intents.none()
    intents.guilds = True

    bot = commands.AutoShardedBot(command_prefix="!", intents=intents)

    @bot.tree.command(name="ping", description="Liveness check.")
    async def ping(interaction: discord.Interaction) -> None:  # pragma: no cover - needs gateway
        await interaction.response.send_message("pong", ephemeral=True)

    @bot.event
    async def on_ready() -> None:  # pragma: no cover - needs gateway
        # Global command sync is slow to propagate; per-guild sync is used in
        # Phase 3 for fast iteration (PLAN.md §3.2 slash-command sync strategy).
        await bot.tree.sync()

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


__all__ = ["bot", "build_bot", "run", "list_command_names", "app_commands"]
