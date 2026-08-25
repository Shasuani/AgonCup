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
# Owner decision (2026-08-25): the single/double-elimination question is not asked
# anywhere (bot or web) — every tournament is a plain knockout bracket: lose and
# you are out, until one team is left. Matches themselves are decided in-game
# (5v5 at the nexus, 1v1 ARAM at first blood). The backend still accepts
# ``double_elimination`` over the API should it ever be wanted back.
_KNOCKOUT_FORMAT = "single_elimination"
_SERIES_OPTIONS = [
    discord.SelectOption(label="Best of 1 (single game)", value="1"),
    discord.SelectOption(label="Best of 3", value="3"),
    discord.SelectOption(label="Best of 5", value="5"),
]
_SIZE_OPTIONS = [
    discord.SelectOption(label=f"{n} teams", value=str(n))
    for n in range(_MIN_TEAMS, _MAX_TEAMS + 1)
]
# Riot platform the tournament is played on. Entrants must hold a VERIFIED account
# on it (an LCU lobby invite is per-server — an EUW client cannot invite an EUNE
# player), so the join code bounces anyone who doesn't. EUNE/EUW lead the list
# (where our guilds live); KR is deliberately absent (out of scope — compliance).
_REGION_LABELS: dict[str, str] = {
    "eun1": "EUNE",
    "euw1": "EUW",
    "na1": "NA",
    "br1": "BR",
    "la1": "LAN",
    "la2": "LAS",
    "oc1": "OCE",
    "tr1": "TR",
    "ru": "RU",
    "jp1": "JP",
}
_REGION_OPTIONS = [
    discord.SelectOption(label=label, value=value) for value, label in _REGION_LABELS.items()
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
    game_mode: str
    best_of: int
    region: str


class BasicsModal(discord.ui.Modal, title="New tournament — settings"):
    """Form 1: the five REQUIRED settings.

    Why a modal rather than slash options: as slash options the admin answers them
    one prompt at a time in the command bar, with no way to review or correct an
    earlier answer before sending. A modal shows all five at once, and the four
    dropdowns (``required=True``) make size/mode/series/region unmissable and
    untypoable.

    These are exactly the settings the web console's create form asks for, so a
    tournament made here and one made there are the same object. Bracket format is
    deliberately NOT asked, here or on the web: every tournament is a knockout
    bracket (see ``_KNOCKOUT_FORMAT``).

    Discord caps a modal at 5 components — this now sits exactly AT the cap, so
    the next required setting must displace one of these into a follow-up view.
    The two optional scheduling fields still live in a second form
    (:class:`ScheduleModal`) reached from :class:`ScheduleView`: a modal cannot be
    opened directly from a modal submit.
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
        self.mode_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_MODE_OPTIONS, required=True
        )
        self.series_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_SERIES_OPTIONS, required=True
        )
        self.region_select: discord.ui.Select[BasicsModal] = discord.ui.Select(
            options=_REGION_OPTIONS, required=True
        )
        self.add_item(discord.ui.Label(text="Tournament name", component=self.name_input))
        self.add_item(
            discord.ui.Label(
                text="Bracket size",
                description="How many teams/players can enter. Non-powers of two get byes.",
                component=self.size_select,
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
        self.add_item(
            discord.ui.Label(
                text="Region",
                description="The server it is played on — players need a verified account there.",
                component=self.region_select,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        basics = TournamentBasics(
            name=self.name_input.value.strip(),
            max_teams=int(self.size_select.values[0]),
            game_mode=self.mode_select.values[0],
            best_of=int(self.series_select.values[0]),
            region=self.region_select.values[0],
        )
        await interaction.response.send_message(
            embed=info_embed(
                "Step 2 — scheduling (optional)",
                f"**{basics.name}** · {basics.max_teams} teams · "
                f"`{basics.game_mode}` · Bo{basics.best_of} · "
                f"{_REGION_LABELS.get(basics.region, basics.region.upper())}\n\n"
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
                format=_KNOCKOUT_FORMAT,
                game_mode=basics.game_mode,
                best_of=basics.best_of,
                region=basics.region,
                registration_opens_at=registration_opens_at,
                start_at=start_at,
            )
        except BackendError as exc:
            await interaction.followup.send(embed=error_embed(exc), ephemeral=True)
            return
        region_value = t.get("region")
        embed = embed_from(
            tournament_created(
                name=t["name"],
                tournament_id=str(t.get("id", "")),
                mode=t.get("game_mode"),
                best_of=t.get("best_of"),
                region=(
                    _REGION_LABELS.get(region_value, str(region_value).upper())
                    if region_value
                    else None
                ),
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
    @app_commands.describe(tournament="Join code, name, or id of the tournament.")
    @app_commands.default_permissions(manage_guild=True)
    async def start(self, interaction: discord.Interaction, tournament: str) -> None:
        """Generate the bracket, start the tournament, and open round one.

        The join code leads the option's description because it is what the admin
        has in front of them — ``/tournament-create`` printed it two messages ago.
        The backend resolves any of the three (code / name / id).
        """
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
                f"**{res.get('rounds', '?')}** rounds, **{res.get('byes', 0)}** byes.\n"
                "Every playable match has been handed to its players — whoever is "
                "opening each lobby has been DMed the name and password.",
            ),
            ephemeral=True,
        )


__all__ = ["BasicsModal", "ScheduleModal", "ScheduleView", "TournamentAdminCog"]
