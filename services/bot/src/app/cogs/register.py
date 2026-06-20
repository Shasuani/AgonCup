"""``/register`` slash command — link a Riot account (PLAN.md §3.2 / §3.5).

Thin presentation layer: the cog reads the player-supplied Riot ID, delegates to
the backend bridge (which delegates to ``app.accounts.service.link_account`` and
the Riot Account-V1 client), and renders the returned verification instructions as
a ``lol_discord`` embed. NO Account-V1 logic lives here.

Import-safe: importing this module does not connect the gateway; the command runs
only when discord.py dispatches an interaction.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import registration_started, registration_verified

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed


class RegisterCog(commands.Cog):
    """Account-linking command group."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend

    @app_commands.command(
        name="register",
        description="Link your Riot account and get verification instructions.",
    )
    @app_commands.describe(
        game_name="Your Riot ID game name (before the #).",
        tag_line="Your Riot ID tag line (after the #).",
        region="Your platform region, e.g. euw1, na1.",
    )
    async def register(
        self,
        interaction: discord.Interaction,
        game_name: str,
        tag_line: str,
        region: str,
    ) -> None:
        """Start Riot account verification for the invoking user (ephemeral)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            result = await self._backend.link_account(
                ctx, game_name=game_name, tag_line=tag_line, region=region
            )
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return

        riot_id = f"{result.get('game_name', game_name)}#{result.get('tag_line', tag_line)}"
        if result.get("verified"):
            embed = embed_from(registration_verified(riot_id=riot_id))
        else:
            target = str(result.get("target_icon_id", "the assigned"))
            embed = embed_from(registration_started(riot_id=riot_id, target_icon=target))
        await interaction.followup.send(embed=embed, ephemeral=True)


__all__ = ["RegisterCog"]
