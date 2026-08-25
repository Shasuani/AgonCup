"""Cog registration tests (PLAN.md §3.2 slash commands).

Offline by design: ``setup_cogs`` adds the command cogs to a freshly built bot
without any gateway connection. The bot is now the ADMIN + onboarding surface
only — user-facing team/account/tournament actions moved to the desktop client —
so we assert exactly the surviving commands are present and the removed ones are
gone, plus that the admin commands carry Manage-Guild default permissions.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import discord

from app.backend_client import BackendError, DiscordContext
from app.cogs import setup_cogs
from app.cogs._helpers import context_of, error_embed
from app.cogs.register import RegisterCog
from app.cogs.tournament import (
    BasicsModal,
    ScheduleModal,
    ScheduleView,
    TournamentAdminCog,
    TournamentBasics,
)
from app.main import build_bot


def _registered_names(bot: Any) -> set[str]:
    return {cmd.name for cmd in bot.tree.get_commands()}


def test_setup_cogs_registers_only_surviving_commands() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    names = _registered_names(bot)
    # Surviving: sign-in + admin tournament management.
    assert {"register", "link", "tournament-create", "tournament-start"} <= names
    # Removed: user-facing actions now live in the desktop client.
    assert not ({"accounts", "unlink", "team", "tournament"} & names)


def _make_interaction() -> Any:
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    member = _Member(7, admin=True, name="Admin")
    interaction.guild_id = 99
    interaction.user = member
    return interaction


def _sent_embed(interaction: Any) -> discord.Embed:
    interaction.followup.send.assert_awaited_once()
    return interaction.followup.send.await_args.kwargs["embed"]


def test_register_renders_minted_code() -> None:
    backend = AsyncMock()
    backend.start_registration.return_value = {"code": "WXYZ-2345"}
    cog = RegisterCog(backend)
    interaction = _make_interaction()

    asyncio.run(cog.register.callback(cog, interaction))

    backend.start_registration.assert_awaited_once()
    embed = _sent_embed(interaction)
    blob = " ".join(f.value or "" for f in embed.fields)
    assert "WXYZ-2345" in blob
    assert "sign in" in (embed.title or "").lower()


def test_link_alias_renders_minted_code() -> None:
    backend = AsyncMock()
    backend.start_registration.return_value = {"code": "WXYZ-2345"}
    cog = RegisterCog(backend)
    interaction = _make_interaction()

    asyncio.run(cog.link.callback(cog, interaction))

    backend.start_registration.assert_awaited_once()
    blob = " ".join(f.value or "" for f in _sent_embed(interaction).fields)
    assert "WXYZ-2345" in blob


def _fill_basics(
    cog: TournamentAdminCog,
    interaction: Any,
    *,
    name: str = "Summer Cup",
    size: str = "8",
    mode: str = "summoners_rift_5v5",
    series: str = "1",
) -> ScheduleView:
    """Run the slash command, fill form 1 and submit it; return the step-2 view."""
    asyncio.run(cog.create.callback(cog, interaction))
    modal: BasicsModal = interaction.response.send_modal.await_args.args[0]
    modal.name_input._value = name
    modal.size_select._values = [size]
    modal.mode_select._values = [mode]
    modal.series_select._values = [series]
    asyncio.run(modal.on_submit(interaction))
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, ScheduleView)
    return view


def _run_create(
    cog: TournamentAdminCog, interaction: Any, *, opens: str = "", starts: str = ""
) -> ScheduleModal:
    """Drive the whole flow: command -> basics form -> "Add dates…" -> schedule form."""
    view = _fill_basics(cog, interaction)
    asyncio.run(view.schedule.callback(interaction))
    modal: ScheduleModal = interaction.response.send_modal.await_args.args[0]
    modal.registration_opens._value = opens
    modal.starts._value = starts
    asyncio.run(modal.on_submit(interaction))
    return modal


def test_tournament_create_shows_join_code() -> None:
    backend = AsyncMock()
    backend.create_tournament.return_value = {
        "id": "abcd1234-0000-0000-0000-000000000000",
        "name": "Summer Cup",
        "format": "single_elimination",
        "game_mode": "summoners_rift_5v5",
        "best_of": 1,
        "join_code": "ABCD2345",
    }
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    _run_create(cog, interaction)

    backend.create_tournament.assert_awaited_once()
    kwargs = backend.create_tournament.await_args.kwargs
    assert kwargs["max_teams"] == 8
    assert kwargs["format"] == "single_elimination"
    assert kwargs["game_mode"] == "summoners_rift_5v5"
    assert kwargs["best_of"] == 1
    embed = _sent_embed(interaction)
    blob = " ".join((f.name or "") + (f.value or "") for f in embed.fields)
    assert "ABCD2345" in blob
    assert "desktop client" in blob.lower()


def test_tournament_create_asks_everything_in_a_form() -> None:
    """The command takes no options — every setting is asked for in form 1.

    As optional slash parameters the core settings sat behind the "+"
    affordance, and an admin who never opened it silently got 16 teams / 5v5 / Bo1.

    Bracket format is deliberately absent: every tournament is a knockout
    bracket (owner decision, 2026-08-25) — the single/double-elimination
    question is asked nowhere.
    """
    assert TournamentAdminCog.create.parameters == []

    modal = BasicsModal(cog=TournamentAdminCog(AsyncMock()))
    labels = [child for child in modal.children if isinstance(child, discord.ui.Label)]

    assert [label.text for label in labels] == [
        "Tournament name",
        "Bracket size",
        "Game mode",
        "Series length",
    ]
    assert all(label.component.required for label in labels)
    # Discord's hard cap on a modal is 5 — further settings belong in the schedule form.
    assert len(labels) <= 5
    # The bracket-size dropdown mirrors the backend's MAX_TEAMS_HARD_CAP.
    assert [o.value for o in modal.size_select.options] == [str(n) for n in range(2, 17)]
    assert [o.value for o in modal.mode_select.options] == ["summoners_rift_5v5", "aram_1v1"]
    assert [o.value for o in modal.series_select.options] == ["1", "3", "5"]


def test_tournament_create_always_sends_the_knockout_format() -> None:
    """The backend always receives the fixed knockout format.

    The form asks no single/double-elimination question (owner decision): every
    tournament is a plain knockout bracket, so the cog pins ``single_elimination``.
    """
    backend = AsyncMock()
    backend.create_tournament.return_value = {"id": "t1", "name": "Summer Cup"}
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    view = _fill_basics(cog, interaction)
    asyncio.run(view.skip.callback(interaction))

    assert backend.create_tournament.await_args.kwargs["format"] == "single_elimination"


def test_basics_form_hands_off_to_the_optional_schedule_form() -> None:
    """Form 1 cannot open form 2 directly — Discord forbids modal-after-modal."""
    cog = TournamentAdminCog(AsyncMock())
    interaction = _make_interaction()

    view = _fill_basics(
        cog,
        interaction,
        name="  Summer Cup  ",
        size="16",
        series="3",
    )

    assert view._basics == TournamentBasics(
        name="Summer Cup",  # trimmed
        max_teams=16,
        game_mode="summoners_rift_5v5",
        best_of=3,
    )
    assert [b.label for b in view.children] == ["Add dates…", "Skip — create now"]


def test_skip_button_creates_without_a_schedule() -> None:
    backend = AsyncMock()
    backend.create_tournament.return_value = {"id": "t1", "name": "Summer Cup"}
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    view = _fill_basics(cog, interaction)
    asyncio.run(view.skip.callback(interaction))

    # The buttons are dropped so the tournament cannot be created twice.
    assert interaction.response.edit_message.await_args.kwargs["view"] is None
    kwargs = backend.create_tournament.await_args.kwargs
    assert (kwargs["registration_opens_at"], kwargs["start_at"]) == (None, None)


def test_schedule_modal_fields_are_optional_and_labelled() -> None:
    """The scheduling fields moved into a modal precisely so they can be SKIPPED.

    Discord walks through required options only, and will not accept an empty
    required option — so an optional-but-prompted field is only expressible as a
    modal input with required=False.
    """
    modal = ScheduleModal(
        cog=TournamentAdminCog(AsyncMock()),
        basics=TournamentBasics(
            name="Cup",
            max_teams=8,
            game_mode="aram_1v1",
            best_of=1,
        ),
    )
    fields = [modal.registration_opens, modal.starts]
    labels = [child for child in modal.children if isinstance(child, discord.ui.Label)]

    assert [f.required for f in fields] == [False, False]
    assert all("(optional)" in label.text for label in labels)
    assert all("leave blank" in (f.placeholder or "").lower() for f in fields)


def test_tournament_create_skips_scheduling_when_the_modal_is_left_blank() -> None:
    backend = AsyncMock()
    backend.create_tournament.return_value = {"id": "t1", "name": "Summer Cup"}
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    _run_create(cog, interaction, opens="", starts="")

    kwargs = backend.create_tournament.await_args.kwargs
    assert kwargs["registration_opens_at"] is None
    assert kwargs["start_at"] is None


def test_tournament_create_passes_modal_times_through_as_utc_iso() -> None:
    backend = AsyncMock()
    backend.create_tournament.return_value = {"id": "t1", "name": "Summer Cup"}
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    _run_create(cog, interaction, opens="2026-08-01 18:00", starts="2026-08-02 20:00")

    kwargs = backend.create_tournament.await_args.kwargs
    assert kwargs["registration_opens_at"].startswith("2026-08-01T18:00")
    assert kwargs["start_at"].startswith("2026-08-02T20:00")


def test_tournament_create_rejects_an_unreadable_time() -> None:
    backend = AsyncMock()
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    _run_create(cog, interaction, opens="next tuesday")

    backend.create_tournament.assert_not_awaited()
    message = interaction.followup.send.await_args.args[0]
    assert "next tuesday" in message


def test_tournament_start_calls_backend() -> None:
    backend = AsyncMock()
    backend.start_tournament.return_value = {"matches_created": 4, "rounds": 2, "byes": 0}
    cog = TournamentAdminCog(backend)
    interaction = _make_interaction()

    asyncio.run(cog.start.callback(cog, interaction, "Summer Cup"))

    backend.start_tournament.assert_awaited_once()
    assert "started" in (_sent_embed(interaction).title or "").lower()


def test_admin_commands_require_manage_guild() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    for name in ("tournament-create", "tournament-start"):
        cmd = next(c for c in bot.tree.get_commands() if c.name == name)
        assert cmd.default_permissions is not None
        assert cmd.default_permissions.manage_guild is True


# ---------------------------------------------------------------------------
# context_of: the asserted Discord context (Manage-Guild => is_discord_guild_admin)
# ---------------------------------------------------------------------------
class _Perms:
    def __init__(self, manage_guild: bool) -> None:
        self.manage_guild = manage_guild


class _Member:
    def __init__(self, uid: int, *, admin: bool, name: str) -> None:
        self.id = uid
        self.guild_permissions = _Perms(admin)
        self.display_name = name


class _Interaction:
    def __init__(self, *, guild_id: int, member: _Member) -> None:
        self.guild_id = guild_id
        self.user = member


def test_context_of_reads_manage_guild() -> None:
    member = _Member(7, admin=True, name="Cap")
    ctx = context_of(_Interaction(guild_id=99, member=member))  # type: ignore[arg-type]
    assert ctx == DiscordContext(
        discord_user_id=7,
        discord_guild_id=99,
        is_discord_guild_admin=True,
        display_name="Cap",
    )


def test_context_of_non_admin() -> None:
    member = _Member(8, admin=False, name="Player")
    ctx = context_of(_Interaction(guild_id=99, member=member))  # type: ignore[arg-type]
    assert ctx.is_discord_guild_admin is False


def test_error_embed_surfaces_backend_detail() -> None:
    embed = error_embed(BackendError(409, "guild is not onboarded"))
    assert isinstance(embed, discord.Embed)
    assert "guild is not onboarded" in (embed.description or "")
