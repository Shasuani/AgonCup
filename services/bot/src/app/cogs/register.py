"""``/register`` slash command — link a Riot account (PLAN.md §3.2 / §3.5).

Thin presentation layer: the cog delegates to the backend bridge (which mints a
one-time desktop-verification code) and renders the returned code as a
``lol_discord`` embed. Linking is finished in the LeagueOfLeagues desktop app:
the desktop reads the player's PUUID from the local League client (LCU) and
completes the link with this code — so the bot no longer takes a Riot ID and
verification does NOT complete here.

Import-safe: importing this module does not connect the gateway; the command runs
only when discord.py dispatches an interaction.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import registration_code

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed


class RegisterCog(commands.Cog):
    """Account-linking command group."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend

    @app_commands.command(
        name="register",
        description="Get a one-time code to link your account in the desktop app.",
    )
    async def register(self, interaction: discord.Interaction) -> None:
        """Mint a desktop-verification code for the invoking user (ephemeral).

        The origin guild is carried implicitly in the interaction context; the
        desktop app finishes verification, so no Riot ID is needed here.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            result = await self._backend.start_registration(ctx)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return

        embed = embed_from(registration_code(code=str(result.get("code", ""))))
        await interaction.followup.send(embed=embed, ephemeral=True)


__all__ = ["RegisterCog"]
