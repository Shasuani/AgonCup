"""``/register`` slash command — sign in + link a Riot account (PLAN.md §3.2 / §3.5).

Thin presentation layer: the cog delegates to the backend bridge (which mints a
one-time code) and renders the returned code as a ``lol_discord`` embed. The code
is pasted into the LeagueOfLeagues desktop app to **sign in**; the desktop then
reads the player's PUUID from the local League client (LCU) and completes the
account link — so the bot no longer takes a Riot ID and verification does NOT
complete here. ``/link`` is an alias of ``/register`` (same backend call).

Import-safe: importing this module does not connect the gateway; the command runs
only when discord.py dispatches an interaction.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import registration_code

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed, info_embed


def _account_line(a: dict[str, Any]) -> str:
    """One display line for a linked account in the ``/accounts`` list."""
    game_name = a.get("game_name") or "?"
    tag_line = a.get("tag_line") or "?"
    region = (a.get("region") or "?").upper()
    rank = a.get("rank_text") or "Unranked"
    mark = "✅" if a.get("verified") else "⏳"
    return f"{mark} **{game_name}#{tag_line}** — {region} · {rank}"


def _split_riot_id(riot_id: str) -> tuple[str, str]:
    """Split a ``Name#TAG`` Riot ID into ``(game_name, tag_line)``.

    Raises:
        ValueError: the input is not a single ``Name#TAG`` pair.
    """
    game_name, sep, tag_line = riot_id.strip().partition("#")
    game_name = game_name.strip()
    tag_line = tag_line.strip()
    if not sep or not game_name or not tag_line:
        raise ValueError("riot id must look like Name#TAG")
    return game_name, tag_line


class RegisterCog(commands.Cog):
    """Account-linking command group."""

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

    @app_commands.command(
        name="accounts",
        description="Show the League accounts you've linked.",
    )
    async def accounts(self, interaction: discord.Interaction) -> None:
        """List the invoking user's own linked Riot accounts (ephemeral)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            rows = await self._backend.list_my_accounts(ctx)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        if not rows:
            body = (
                "You haven't linked any accounts yet. Run `/register` and finish "
                "linking in the desktop app."
            )
        else:
            body = "\n".join(_account_line(a) for a in rows)
        await interaction.followup.send(
            embed=info_embed("Your linked accounts", body), ephemeral=True
        )

    @app_commands.command(
        name="unlink",
        description="Remove one of your linked League accounts.",
    )
    @app_commands.describe(riot_id="The Riot ID to remove, e.g. Name#TAG.")
    async def unlink(self, interaction: discord.Interaction, riot_id: str) -> None:
        """Remove one of the invoking user's own linked accounts by Riot ID."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            game_name, tag_line = _split_riot_id(riot_id)
        except ValueError:
            await interaction.followup.send(
                embed=info_embed("Invalid Riot ID", "Use the form `Name#TAG`, e.g. `Faker#KR1`."),
                ephemeral=True,
            )
            return
        ctx = context_of(interaction)
        try:
            await self._backend.unlink_account(ctx, game_name=game_name, tag_line=tag_line)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=info_embed("Account removed", f"Unlinked **{game_name}#{tag_line}**."),
            ephemeral=True,
        )


__all__ = ["RegisterCog"]
