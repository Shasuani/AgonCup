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


class ScheduleModal(discord.ui.Modal, title="Schedule (optional)"):
    """The two optional scheduling fields, asked as a form after the required ones.

    Why a modal at all: Discord only walks the admin through **required** options,
    and a required option cannot be submitted empty. So "ask for the optional ones
    in order too, and let me skip them with an empty answer" is not expressible as
    slash-command options. A modal is — both inputs are ``required=False``, so the
    admin sees them labelled, in order, and submits the form untouched to skip.

    It must be the interaction's FIRST response (Discord forbids a modal after a
    defer), which is why :meth:`TournamentAdminCog.create` does not defer.
    """

    registration_opens: discord.ui.TextInput[ScheduleModal] = discord.ui.TextInput(
        label="Registration opens — UTC (optional)",
        placeholder="2026-08-01 18:00 — leave blank to skip",
        required=False,
        max_length=32,
    )
    starts: discord.ui.TextInput[ScheduleModal] = discord.ui.TextInput(
        label="Starts — UTC (optional)",
        placeholder="2026-08-02 20:00 — leave blank to skip",
        required=False,
        max_length=32,
    )

    def __init__(
        self,
        *,
        cog: TournamentAdminCog,
        name: str,
        max_teams: int,
        game_mode: str,
        best_of: int,
    ) -> None:
        super().__init__()
        self._cog = cog
        self._name = name
        self._max_teams = max_teams
        self._game_mode = game_mode
        self._best_of = best_of

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            opens_iso = _parse_utc(self.registration_opens.value)
            start_iso = _parse_utc(self.starts.value)
        except ValueError as exc:
            await interaction.followup.send(
                f"⚠️ {exc} Use a UTC time like `2026-08-01 18:00`.", ephemeral=True
            )
            return
        await self._cog.create_tournament(
            interaction,
            name=self._name,
            max_teams=self._max_teams,
            game_mode=self._game_mode,
            best_of=self._best_of,
            registration_opens_at=opens_iso,
            start_at=start_iso,
        )


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
        max_teams="Bracket size: how many teams/players can enter (2-16).",
        mode="Game mode: 5v5 Summoner's Rift or 1v1 ARAM duel.",
        series="Series length per match: best of 1, 3, or 5.",
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
        max_teams: app_commands.Range[int, 2, 16],
        mode: app_commands.Choice[str],
        series: app_commands.Choice[int],
    ) -> None:
        """Create a tournament (guild admin; backend re-checks the role).

        Every setting is asked for, in order, and none of them can be missed:

        * ``name``/``max_teams``/``mode``/``series`` are REQUIRED slash options —
          required options are what Discord walks the admin through one at a time,
          and dropdowns keep ``mode``/``series`` unambiguous. As optional
          parameters (the old shape) all four sat behind the "+" affordance, so an
          admin who never opened it silently got 16 teams, 5v5 and Bo1.
        * the two **scheduling** fields follow in a :class:`ScheduleModal`,
          labelled "(optional)" and skippable by submitting the form untouched.
          They cannot be slash options and still behave that way: Discord neither
          walks through optional options nor accepts an empty required one.

        ``Range[int, 2, 16]`` mirrors the backend's ``MAX_TEAMS_HARD_CAP`` so the
        client rejects an out-of-range bracket before a round trip.

        No ``defer`` here — sending a modal must be the interaction's first
        response, and the modal defers on submit instead.
        """
        await interaction.response.send_modal(
            ScheduleModal(
                cog=self,
                name=name,
                max_teams=int(max_teams),
                game_mode=mode.value,
                best_of=series.value,
            )
        )

    async def create_tournament(
        self,
        interaction: discord.Interaction,
        *,
        name: str,
        max_teams: int,
        game_mode: str,
        best_of: int,
        registration_opens_at: str | None,
        start_at: str | None,
    ) -> None:
        """Create the tournament and render the result (called from the modal).

        Split out of :meth:`create` because the settings now arrive across two
        interactions: the slash options, then the schedule modal.
        """
        try:
            t = await self._backend.create_tournament(
                context_of(interaction),
                name=name,
                max_teams=max_teams,
                game_mode=game_mode,
                best_of=best_of,
                registration_opens_at=registration_opens_at,
                start_at=start_at,
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
