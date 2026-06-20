"""Slash-command cogs for the LeagueOfLeagues bot (PLAN.md §3.2).

Each cog is a THIN presentation layer: it reads the interaction's Discord context,
delegates to :class:`app.backend_client.BackendClient`, and renders the result as
a ``lol_discord`` embed. NO game logic lives here.

Importing this package wires nothing to the gateway; cogs are added to a bot via
:func:`setup_cogs` (an async helper the bot calls in ``setup_hook``), so importing
in tests stays offline.
"""

from __future__ import annotations

from discord.ext import commands

from app.backend_client import BackendClient
from app.cogs.register import RegisterCog
from app.cogs.team import TeamCog
from app.cogs.tournament import TournamentAdminCog, TournamentCog


async def setup_cogs(
    bot: commands.Bot | commands.AutoShardedBot, *, backend: BackendClient | None = None
) -> None:
    """Register every command cog on ``bot`` (called from ``setup_hook``).

    A single shared :class:`BackendClient` is injected into all cogs so tests can
    pass a stub and the bot reuses one configured client.
    """
    client = backend or BackendClient()
    await bot.add_cog(RegisterCog(client))
    await bot.add_cog(TeamCog(client))
    await bot.add_cog(TournamentCog(client))
    await bot.add_cog(TournamentAdminCog(client))


__all__ = [
    "RegisterCog",
    "TeamCog",
    "TournamentAdminCog",
    "TournamentCog",
    "setup_cogs",
]
