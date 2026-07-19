"""lol-discord: shared Discord helpers (embed builder + error formatter).

Framework-light Phase-0 skeleton (PLAN.md §3.2 / §5). The embed builder returns
a plain dict (Discord embed JSON shape) so this lib carries no hard discord.py
dependency.
"""

from __future__ import annotations

from lol_discord.embeds import EmbedColor, EmbedField, build_embed
from lol_discord.errors import format_error
from lol_discord.notifications import (
    build_central_feed_item,
    match_ready,
    match_result,
    registration_approved,
    registration_code,
    registration_verified,
    team_created,
    team_joined,
    tournament_announced,
    tournament_check_in,
    tournament_created,
    tournament_open,
    tournament_started,
)

__all__ = [
    "EmbedColor",
    "EmbedField",
    "build_embed",
    "format_error",
    "registration_approved",
    "registration_code",
    "registration_verified",
    "team_created",
    "team_joined",
    "tournament_created",
    "tournament_open",
    "tournament_check_in",
    "tournament_started",
    "tournament_announced",
    "match_ready",
    "match_result",
    "build_central_feed_item",
]

__version__ = "0.0.0"
