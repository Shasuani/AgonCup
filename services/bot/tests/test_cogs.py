"""Cog registration tests (PLAN.md §3.2 slash commands).

Offline by design: ``setup_cogs`` adds the command cogs to a freshly built bot
without any gateway connection. We assert the expected application commands /
groups are present and that the admin command carries Manage-Guild default
permissions.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import discord

from app.backend_client import BackendError, DiscordContext
from app.cogs import setup_cogs
from app.cogs._helpers import context_of, error_embed
from app.main import build_bot


def _registered_names(bot: Any) -> set[str]:
    return {cmd.name for cmd in bot.tree.get_commands()}


def test_setup_cogs_registers_commands() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    names = _registered_names(bot)
    assert {"register", "team", "tournament", "tournament-create"} <= names


def test_team_group_has_subcommands() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    team = next(c for c in bot.tree.get_commands() if c.name == "team")
    sub = {c.name for c in team.commands}  # type: ignore[union-attr]
    assert {"create", "join", "leave"} <= sub


def test_tournament_group_has_subcommands() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    tournament = next(c for c in bot.tree.get_commands() if c.name == "tournament")
    sub = {c.name for c in tournament.commands}  # type: ignore[union-attr]
    assert {"list", "join", "leave", "status"} <= sub


def test_tournament_create_requires_manage_guild() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    create = next(c for c in bot.tree.get_commands() if c.name == "tournament-create")
    assert create.default_permissions is not None
    assert create.default_permissions.manage_guild is True


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
