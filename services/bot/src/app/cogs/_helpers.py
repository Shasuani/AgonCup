"""Shared cog helpers: Discord-context extraction + error rendering (PLAN.md §3.2).

Keeps every command handler thin and consistent: one place derives the asserted
:class:`~app.backend_client.DiscordContext` from an interaction (including the
``is_discord_guild_admin`` Manage-Guild check the trust boundary depends on), and
one place turns a :class:`~app.backend_client.BackendError` into a user-facing
ephemeral embed.
"""

from __future__ import annotations

from typing import Any

import discord
from lol_discord import format_error
from lol_discord.embeds import EmbedColor, build_embed

from app.backend_client import BackendError, DiscordContext


def context_of(interaction: discord.Interaction) -> DiscordContext:
    """Build the asserted Discord context from an interaction (PLAN.md §3.2).

    ``is_discord_guild_admin`` is the invoking member's **Manage-Guild**
    permission — the single signal the backend trusts for guild-admin actions
    (the bot is the sole token holder, so it reports the gateway-provided member
    permission faithfully). Outside a guild the flag is ``False``.
    """
    guild_id = interaction.guild_id or 0
    member = interaction.user
    is_admin = False
    perms = getattr(member, "guild_permissions", None)
    if perms is not None:
        is_admin = bool(getattr(perms, "manage_guild", False))
    return DiscordContext(
        discord_user_id=interaction.user.id,
        discord_guild_id=guild_id,
        is_discord_guild_admin=is_admin,
        display_name=getattr(member, "display_name", None),
    )


def error_embed(exc: Exception) -> discord.Embed:
    """Render an exception as an ephemeral error embed.

    ``BackendError`` surfaces the backend's ``detail`` (already user-safe);
    anything else gets a generic message so internals never leak.
    """
    if isinstance(exc, BackendError):
        message = exc.detail
    else:
        message = "Something went wrong. Please try again."
    # format_error neutralizes mentions + bounds length; strip its "Error: " prefix
    # since the embed title already says "Error".
    safe = format_error(message).removeprefix("Error: ")
    return discord.Embed.from_dict(build_embed("Error", safe, color=EmbedColor.ERROR))


def embed_from(payload: dict[str, Any]) -> discord.Embed:
    """Convert a ``lol_discord`` embed dict into a ``discord.Embed``."""
    return discord.Embed.from_dict(payload)


def info_embed(title: str, description: str) -> discord.Embed:
    """A simple informational embed (e.g. list output)."""
    return discord.Embed.from_dict(build_embed(title, description, color=EmbedColor.DEFAULT))


__all__ = ["context_of", "embed_from", "error_embed", "info_embed"]
