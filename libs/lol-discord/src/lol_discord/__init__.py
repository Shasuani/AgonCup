"""lol-discord: shared Discord helpers (embed builder + error formatter).

Framework-light Phase-0 skeleton (PLAN.md §3.2 / §5). The embed builder returns
a plain dict (Discord embed JSON shape) so this lib carries no hard discord.py
dependency.
"""

from __future__ import annotations

from lol_discord.embeds import EmbedColor, EmbedField, build_embed
from lol_discord.errors import format_error

__all__ = [
    "EmbedColor",
    "EmbedField",
    "build_embed",
    "format_error",
]

__version__ = "0.0.0"
