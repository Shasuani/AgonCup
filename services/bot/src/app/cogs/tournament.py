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

from dataclasses import dataclass
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from lol_discord import tournament_created

from app.backend_client import BackendClient, BackendError
from app.cogs._helpers import context_of, embed_from, error_embed, info_embed

# Bracket sizes offered in the form. Mirrors the backend's MAX_TEAMS_HARD_CAP, so
# an out-of-range bracket is unpickable rather than rejected after a round trip.
_MIN_TEAMS, _MAX_TEAMS = 2, 16

_MODE_OPTIONS = [
    discord.SelectOption(label="5v5 — Summoner's Rift", value="summoners_rift_5v5"),
    discord.SelectOption(label="1v1 — ARAM duel", value="aram_1v1"),
]
_FORMAT_OPTIONS = [
    discord.SelectOption(label="Single elimination", value="single_elimination"),
    discord.SelectOption(label="Double elimination", value="double_elimination"),
]
_SERIES_OPTIONS = [
    discord.SelectOption(label="Best of 1 (single game)", value="1"),
    discord.SelectOption(label="Best of 3", value="3"),
    discord.SelectOption(label="Best of 5", value="5"),
]
_SIZE_OPTIONS = [
    discord.SelectOption(label=f"{n} teams", value=str(n))
    for n in range(_MIN_TEAMS, _MAX_TEAMS + 1)
]


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


@dataclass(frozen=True, slots=True)
class TournamentBasics:
    """The five required settings, carried from the first form to the second."""

    name: str
    max_teams: int
    format: str
    game_mode: str
    best_of: int


class BasicsModal(discord.ui.Modal, title="New tournament — settings"):
    """Form 1: the five REQUIRED settings.

    Why a modal rather than slash options: as slash options the admin answers them
    one prompt at a time in the command bar, with no way to review or correct an
    earlier answer before sending. A modal shows all five at once, and the four
    dropdowns (``required=True``) make size/format/mode/series unmissable and
    untypoable.

    These are exactly the settings the web console's create form asks for, so a
    tournament made here and one made there are the same object — bracket format
    included, which this form used to omit entirely (every Discord-created
    tournament was silently single-elimination).

    Discord caps a modal at 5 components — which this now sits exactly on, and is
    why the two optional scheduling fields live in a second form
    (:class:`ScheduleModal`) reached from :class:`ScheduleView`: a modal cannot be
    opened directly from a modal submit. Any further setting needs that second
    form, not a sixth row here.
    """

    def __init__(self, *, cog: TournamentAdminCog) -> None:
        super().__init__()
        self._cog = cog
        self.name_input: discord.ui.TextInput[BasicsModal] = discord.ui.TextInput(
            placeholder="Summer Cup",
            required=True,
            max_length=80,
        )
        self.size_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_SIZE_OPTIONS, required=True
        )
        self.format_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_FORMAT_OPTIONS, required=True
        )
        self.mode_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_MODE_OPTIONS, required=True
        )
        self.series_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_SERIES_OPTIONS, required=True
        )
        self.add_item(discord.ui.Label(text="Tournament name", component=self.name_input))
        self.add_item(
            discord.ui.Label(
                text="Bracket size",
                description="How many teams/players can enter. Non-powers of two get byes.",
                component=self.size_select,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Format",
                description="Single elimination: one loss is out. Double: a losers bracket.",
                component=self.format_select,
            )
        )
        self.add_item(discord.ui.Label(text="Game mode", component=self.mode_select))
        self.add_item(
            discord.ui.Label(
                text="Series length",
                description="How many games decide one match.",
                component=self.series_select,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        basics = TournamentBasics(
            name=self.name_input.value.strip(),
            max_teams=int(self.size_select.values[0]),
            format=self.format_select.values[0],
            game_mode=self.mode_select.values[0],
            best_of=int(self.series_select.values[0]),
        )
        await interaction.response.send_message(
            embed=info_embed(
                "Step 2 — scheduling (optional)",
                f"**{basics.name}** · {basics.max_teams} teams · "
                f"`{basics.format}` · `{basics.game_mode}` · Bo{basics.best_of}\n\n"
                "Add registration/start times, or skip and set them later.",
            ),
            view=ScheduleView(cog=self._cog, basics=basics),
            ephemeral=True,
        )


class ScheduleView(discord.ui.View):
    """The bridge between the two forms.

    Discord forbids answering a modal submit with another modal, so the second form
    has to hang off a button press. "Skip" is a first-class button so the common
    case (no schedule yet) is one click, not an empty form.
    """

    def __init__(self, *, cog: TournamentAdminCog, basics: TournamentBasics) -> None:
        super().__init__(timeout=600)
        self._cog = cog
        self._basics = basics

    @discord.ui.button(label="Add dates…", style=discord.ButtonStyle.primary, emoji="📅")
    async def schedule(
        self, interaction: discord.Interaction, button: discord.ui.Button[ScheduleView]
    ) -> None:
        await interaction.response.send_modal(ScheduleModal(cog=self._cog, basics=self._basics))

    @discord.ui.button(label="Skip — create now", style=discord.ButtonStyle.secondary)
    async def skip(
        self, interaction: discord.Interaction, button: discord.ui.Button[ScheduleView]
    ) -> None:
        # edit_message IS this interaction's response: it drops the buttons (so the
        # tournament cannot be created twice) and leaves followup.send available.
        await interaction.response.edit_message(view=None)
        self.stop()
        await self._cog.create_tournament(
            interaction, basics=self._basics, registration_opens_at=None, start_at=None
        )


class ScheduleModal(discord.ui.Modal, title="New tournament — schedule"):
    """Form 2: the two OPTIONAL scheduling fields.

    Both inputs are ``required=False``, so submitting the form untouched is a valid
    "no schedule yet" — which is exactly what a slash option cannot express (Discord
    never walks through optional options, and will not accept an empty required one).
    """

    def __init__(self, *, cog: TournamentAdminCog, basics: TournamentBasics) -> None:
        super().__init__()
        self._cog = cog
        self._basics = basics
        self.registration_opens: discord.ui.TextInput[ScheduleModal] = discord.ui.TextInput(
            placeholder="2026-08-01 18:00 — leave blank to skip",
            required=False,
            max_length=32,
        )
        self.starts: discord.ui.TextInput[ScheduleModal] = discord.ui.TextInput(
            placeholder="2026-08-02 20:00 — leave blank to skip",
            required=False,
            max_length=32,
        )
        self.add_item(
            discord.ui.Label(
                text="Registration opens — UTC (optional)",
                component=self.registration_opens,
            )
        )
        self.add_item(discord.ui.Label(text="Starts — UTC (optional)", component=self.starts))

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
            basics=self._basics,
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
    @app_commands.default_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction) -> None:
        """Open the tournament creation form (backend re-checks the guild-admin role).

        The command itself takes no options: every setting is asked for in a form, so
        nothing can be silently missed. Required settings in :class:`BasicsModal`,
        optional scheduling in :class:`ScheduleModal` behind :class:`ScheduleView`.

        No ``defer`` here — sending a modal must be the interaction's first response.
        """
        await interaction.response.send_modal(BasicsModal(cog=self))

    async def create_tournament(
        self,
        interaction: discord.Interaction,
        *,
        basics: TournamentBasics,
        registration_opens_at: str | None,
        start_at: str | None,
    ) -> None:
        """Create the tournament and render the result (called once the forms are done).

        Split out of :meth:`create` because the settings arrive across several
        interactions: the basics form, then either the skip button or the schedule form.
        """
        try:
            t = await self._backend.create_tournament(
                context_of(interaction),
                name=basics.name,
                max_teams=basics.max_teams,
                format=basics.format,
                game_mode=basics.game_mode,
                best_of=basics.best_of,
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


__all__ = ["BasicsModal", "ScheduleModal", "ScheduleView", "TournamentAdminCog"]
