"""``/tournament-create`` + ``/tournament-start`` — admin-only (PLAN.md §3.2).

The user-facing tournament actions (list/join/check-in/leave/status) now live in
the **desktop client**; the bot keeps only the admin surface. ``/tournament-create``
mints a shareable **join code** the admin hands to players — they redeem it in the
client to enrol. Both commands are gated by the Manage-Guild permission AND the
backend re-checks the persisted ``guild_admin`` role (defence in depth).

Thin presentation layer: every handler delegates to the backend bridge and renders
a ``lol_discord`` embed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import tournament_created

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed, info_embed


def _parse_utc(value: str | None) -> str | None:
    """Parse an admin-typed date/time as UTC → ISO-8601 string (or None).

    Accepts ``2026-08-01 18:00`` / ``2026-08-01T18:00`` / a trailing ``Z``; a naive
    time is assumed UTC. Raises ``ValueError`` with a friendly message on bad input.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Couldn't read the time '{value}'.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class TournamentAdminCog(commands.Cog):
    """Admin-only tournament management (create + start)."""

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
        registration_opens="UTC time registration opens, e.g. 2026-08-01 18:00 (optional).",
        starts="UTC scheduled start time, e.g. 2026-08-02 20:00 (optional).",
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
        registration_opens: str | None = None,
        starts: str | None = None,
    ) -> None:
        """Create a tournament (guild admin; backend re-checks the role)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        game_mode = mode.value if mode is not None else "summoners_rift_5v5"
        best_of = series.value if series is not None else 1
        try:
            opens_iso = _parse_utc(registration_opens)
            start_iso = _parse_utc(starts)
        except ValueError as exc:
            await interaction.followup.send(
                f"⚠️ {exc} Use a UTC time like `2026-08-01 18:00`.", ephemeral=True
            )
            return
        try:
            t = await self._backend.create_tournament(
                ctx,
                name=name,
                max_teams=max_teams,
                game_mode=game_mode,
                best_of=best_of,
                registration_opens_at=opens_iso,
                start_at=start_iso,
            )
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        embed = embed_from(
            tournament_created(
                name=t["name"],
                fmt=str(t.get("format", "single_elim")),
                tournament_id=str(t.get("id", "")),
                mode=t.get("game_mode"),
                best_of=t.get("best_of"),
            )
        )
        join_code = t.get("join_code")
        if join_code:
            embed.add_field(
                name="Join code",
                value=(
                    f"`{join_code}`\nShare this with your players — they enter it in the "
                    "**desktop client** to join this tournament."
                ),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="tournament-start",
        description="Start a tournament & generate the bracket (server admins only).",
    )
    @app_commands.describe(tournament="Tournament name (or its id).")
    @app_commands.default_permissions(manage_guild=True)
    async def start(self, interaction: discord.Interaction, tournament: str) -> None:
        """Generate the bracket and start the tournament (backend gates guild-admin)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        ctx = context_of(interaction)
        try:
            res = await self._backend.start_tournament(ctx, tournament=tournament)
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        await interaction.followup.send(
            embed=info_embed(
                "Tournament started",
                f"Bracket generated: **{res.get('matches_created', '?')}** matches, "
                f"**{res.get('rounds', '?')}** rounds, **{res.get('byes', 0)}** byes.",
            ),
            ephemeral=True,
        )


__all__ = ["TournamentAdminCog"]
