"""``/team`` slash command group — create | join | leave (PLAN.md §3.2 / §2 Team).

Thin presentation layer: each subcommand reads the interaction context, delegates
to the backend bridge, and renders a ``lol_discord`` embed. Captain guards,
roster caps and duplicate-member checks all live in the backend team service — the
cog never reimplements them.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import team_created, team_joined

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed, info_embed


class TeamCog(commands.GroupCog, name="team"):
    """The ``/team`` command group."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend
        super().__init__()

    @app_commands.command(name="create", description="Create a new team you captain.")
    @app_commands.describe(name="The team's display name.")
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        """Create a team owned by the invoking user."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            team = await self._backend.create_team(ctx, name=name)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        captain = ctx.display_name or str(ctx.discord_user_id)
        embed = embed_from(team_created(team_name=team["name"], captain_name=captain))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="join", description="Join a team by name (or id).")
    @app_commands.describe(team="The team name to join (or its exact id).")
    async def join(self, interaction: discord.Interaction, team: str) -> None:
        """Join an existing team as a player, by name or id."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            joined = await self._backend.join_team(ctx, team=team)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        member = ctx.display_name or str(ctx.discord_user_id)
        embed = embed_from(
            team_joined(team_name=joined["name"], member_name=member, member_count=0)
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="leave", description="Leave a team you are on.")
    @app_commands.describe(team_id="The team id to leave.")
    async def leave(self, interaction: discord.Interaction, team_id: str) -> None:
        """Leave a team (backend enforces the captain-protected guard)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            await self._backend.leave_team(ctx, team_id=team_id)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=info_embed("Left team", "You have left the team."), ephemeral=True
        )


__all__ = ["TeamCog"]
