"""``/tournament`` group + ``/tournament-create`` (PLAN.md §3.2 / §2 Tournament).

* ``/tournament list|join|leave|status`` — player-facing read/registration actions.
* ``/tournament-create`` — guild-admin only (the cog gates the command with the
  Manage-Guild permission AND the backend re-checks ``is_discord_guild_admin`` /
  a persisted ``guild_admin`` role; defence in depth).

Thin presentation layer: every handler delegates to the backend bridge and renders
a ``lol_discord`` embed. Bracket planning, registration caps and state machine all
live in the backend tournament service.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import tournament_created

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed, info_embed


def _tournament_line(t: dict[str, Any]) -> str:
    """One display line for a tournament in the list output."""
    return f"**{t.get('name', '?')}** — `{t.get('id', '?')}` ({t.get('status', '?')})"


class TournamentCog(commands.GroupCog, name="tournament"):
    """The ``/tournament`` command group (player-facing)."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend
        super().__init__()

    @app_commands.command(name="list", description="List this server's tournaments.")
    async def list_(self, interaction: discord.Interaction) -> None:
        """List the guild's tournaments (ephemeral)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            rows = await self._backend.list_tournaments(ctx)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        if not rows:
            body = "No tournaments yet. An admin can create one with `/tournament-create`."
        else:
            body = "\n".join(_tournament_line(t) for t in rows)
        await interaction.followup.send(embed=info_embed("Tournaments", body), ephemeral=True)

    @app_commands.command(name="join", description="Register your team into a tournament.")
    @app_commands.describe(tournament_id="The tournament id.", team_id="Your team id.")
    async def join(
        self, interaction: discord.Interaction, tournament_id: str, team_id: str
    ) -> None:
        """Register a team into a tournament."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            reg = await self._backend.join_tournament(
                ctx, tournament_id=tournament_id, team_id=team_id
            )
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=info_embed("Registered", f"Registration status: **{reg.get('status', '?')}**."),
            ephemeral=True,
        )

    @app_commands.command(name="leave", description="Withdraw your team from a tournament.")
    @app_commands.describe(tournament_id="The tournament id.", team_id="Your team id.")
    async def leave(
        self, interaction: discord.Interaction, tournament_id: str, team_id: str
    ) -> None:
        """Withdraw a team from a tournament."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            await self._backend.leave_tournament(ctx, tournament_id=tournament_id, team_id=team_id)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=info_embed("Withdrawn", "Your team has been withdrawn."), ephemeral=True
        )

    @app_commands.command(name="status", description="Show a tournament's current status.")
    @app_commands.describe(tournament_id="The tournament id.")
    async def status(self, interaction: discord.Interaction, tournament_id: str) -> None:
        """Show a tournament's current status."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            t = await self._backend.tournament_status(ctx, tournament_id=tournament_id)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        body = (
            f"Status: **{t.get('status', '?')}**\n"
            f"Format: **{t.get('format', '?')}**\n"
            f"Max teams: **{t.get('max_teams', '?')}**"
        )
        await interaction.followup.send(
            embed=info_embed(t.get("name", "Tournament"), body), ephemeral=True
        )


class TournamentAdminCog(commands.Cog):
    """The admin-only ``/tournament-create`` command (PLAN.md §3.2 guild admin)."""

    def __init__(self, backend: BackendClient) -> None:
        self._backend = backend

    @app_commands.command(
        name="tournament-create",
        description="Create a tournament (server admins only).",
    )
    @app_commands.describe(
        name="Tournament name.",
        max_teams="Bracket size (2-16).",
        mode="Game mode: 5v5 Summoner's Rift (default) or 1v1 ARAM duel.",
        series="Series length per match: best of 1 (default), 3, or 5.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="5v5 (Summoner's Rift)", value="summoners_rift_5v5"),
            app_commands.Choice(name="1v1 (ARAM duel)", value="aram_1v1"),
        ]
    )
    @app_commands.choices(
        series=[
            app_commands.Choice(name="Best of 1 (single game)", value=1),
            app_commands.Choice(name="Best of 3", value=3),
            app_commands.Choice(name="Best of 5", value=5),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        max_teams: int = 16,
        mode: app_commands.Choice[str] | None = None,
        series: app_commands.Choice[int] | None = None,
    ) -> None:
        """Create a tournament (guild admin; backend re-checks the role)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        game_mode = mode.value if mode is not None else "summoners_rift_5v5"
        best_of = series.value if series is not None else 1
        try:
            t = await self._backend.create_tournament(
                ctx, name=name, max_teams=max_teams, game_mode=game_mode, best_of=best_of
            )
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        embed = embed_from(
            tournament_created(name=t["name"], fmt=str(t.get("format", "single_elim")))
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


__all__ = ["TournamentAdminCog", "TournamentCog"]
