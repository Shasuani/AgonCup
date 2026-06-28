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
from app.cogs.register import RegisterCog, _account_line, _split_riot_id
from app.main import build_bot


def _registered_names(bot: Any) -> set[str]:
    return {cmd.name for cmd in bot.tree.get_commands()}


def test_setup_cogs_registers_commands() -> None:
    bot = build_bot()
    asyncio.run(setup_cogs(bot, backend=AsyncMock()))

    names = _registered_names(bot)
    assert {
        "register",
        "link",
        "accounts",
        "unlink",
        "team",
        "tournament",
        "tournament-create",
    } <= names


def _make_register_interaction() -> Any:
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    member = _Member(7, admin=False, name="Player")
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
    interaction = _make_register_interaction()

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
    interaction = _make_register_interaction()

    asyncio.run(cog.link.callback(cog, interaction))

    backend.start_registration.assert_awaited_once()
    blob = " ".join(f.value or "" for f in _sent_embed(interaction).fields)
    assert "WXYZ-2345" in blob


def test_accounts_lists_linked() -> None:
    backend = AsyncMock()
    backend.list_my_accounts.return_value = [
        {
            "game_name": "Chama",
            "tag_line": "Shasu",
            "region": "eun1",
            "verified": True,
            "rank_text": "Gold II, 45 LP",
        },
        {
            "game_name": "Smurf",
            "tag_line": "EUNE",
            "region": "eun1",
            "verified": False,
            "rank_text": "Unranked",
        },
    ]
    cog = RegisterCog(backend)
    interaction = _make_register_interaction()

    asyncio.run(cog.accounts.callback(cog, interaction))

    backend.list_my_accounts.assert_awaited_once()
    embed = _sent_embed(interaction)
    blob = (embed.title or "") + (embed.description or "")
    assert "Chama#Shasu" in blob
    assert "Smurf#EUNE" in blob


def test_accounts_empty_prompts_register() -> None:
    backend = AsyncMock()
    backend.list_my_accounts.return_value = []
    cog = RegisterCog(backend)
    interaction = _make_register_interaction()

    asyncio.run(cog.accounts.callback(cog, interaction))

    embed = _sent_embed(interaction)
    assert "/register" in (embed.description or "")


def test_unlink_calls_backend_with_parsed_riot_id() -> None:
    backend = AsyncMock()
    cog = RegisterCog(backend)
    interaction = _make_register_interaction()

    asyncio.run(cog.unlink.callback(cog, interaction, "Chama#Shasu"))

    backend.unlink_account.assert_awaited_once()
    kwargs = backend.unlink_account.await_args.kwargs
    assert kwargs["game_name"] == "Chama"
    assert kwargs["tag_line"] == "Shasu"
    assert "Chama#Shasu" in (_sent_embed(interaction).description or "")


def test_unlink_invalid_riot_id_does_not_call_backend() -> None:
    backend = AsyncMock()
    cog = RegisterCog(backend)
    interaction = _make_register_interaction()

    asyncio.run(cog.unlink.callback(cog, interaction, "no-separator"))

    backend.unlink_account.assert_not_awaited()
    assert "Invalid" in (_sent_embed(interaction).title or "")


def test_split_riot_id_parses_and_rejects() -> None:
    assert _split_riot_id("  Chama#Shasu ") == ("Chama", "Shasu")
    for bad in ["noTag", "#TAG", "Name#", ""]:
        try:
            _split_riot_id(bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_account_line_marks_verified_and_unverified() -> None:
    verified = _account_line(
        {
            "game_name": "Chama",
            "tag_line": "Shasu",
            "region": "eun1",
            "verified": True,
            "rank_text": "Gold II, 45 LP",
        }
    )
    assert "✅" in verified and "Chama#Shasu" in verified and "EUN1" in verified
    pending = _account_line({"game_name": "S", "tag_line": "E", "verified": False})
    assert "⏳" in pending and "Unranked" in pending


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
