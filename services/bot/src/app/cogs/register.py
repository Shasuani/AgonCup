"""``/register`` slash command — sign in to the desktop app (PLAN.md §3.2 / §3.5).

Thin presentation layer: the cog delegates to the backend bridge (which mints a
one-time code) and renders the returned code as a ``lol_discord`` embed. The code
is pasted into the AgonCup desktop app to **sign in**; the desktop then
reads the player's PUUID from the local League client (LCU) and completes the
account link — so the bot no longer takes a Riot ID and verification does NOT
complete here. ``/link`` is an alias of ``/register`` (same backend call).

This is the ONE bot touch a user needs: everything else (linking accounts,
teams, tournaments, unregistering) now happens in the desktop client.

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
    """Sign-in (pairing-code) command group — the desktop bootstrap."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend

    async def _start_registration(self, interaction: discord.Interaction) -> None:
        """Mint a one-time sign-in code for the invoking user (ephemeral).

        The origin guild is carried implicitly in the interaction context; the
        code is pasted into the desktop app to sign in, and the desktop finishes
        the account link, so no Riot ID is needed here.
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

    @app_commands.command(
        name="register",
        description="Get a one-time code to sign in to the desktop app and link your account.",
    )
    async def register(self, interaction: discord.Interaction) -> None:
        """Mint a one-time sign-in code (ephemeral). See :meth:`_start_registration`."""
        await self._start_registration(interaction)

    @app_commands.command(
        name="link",
        description="Get a one-time code to sign in to the desktop app and link your account.",
    )
    async def link(self, interaction: discord.Interaction) -> None:
        """Alias of :meth:`register` — same backend call (mints a sign-in code)."""
        await self._start_registration(interaction)


__all__ = ["RegisterCog"]
