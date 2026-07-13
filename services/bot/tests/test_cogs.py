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
from app.cogs.tournament import TournamentAdminCog
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

    asyncio.run(cog.create.callback(cog, interaction, "Summer Cup"))

    backend.create_tournament.assert_awaited_once()
    embed = _sent_embed(interaction)
    blob = " ".join((f.name or "") + (f.value or "") for f in embed.fields)
    assert "ABCD2345" in blob
    assert "desktop client" in blob.lower()


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
