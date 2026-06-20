"""Embed builder helper.

Returns a plain ``dict`` in Discord's embed JSON shape so the bot can hand it
straight to ``discord.Embed.from_dict`` while this lib stays framework-light and
trivially testable.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, TypedDict

# Discord hard limits (kept local to avoid a discord.py dependency).
_TITLE_MAX = 256
_DESC_MAX = 4096
_FIELD_NAME_MAX = 256
_FIELD_VALUE_MAX = 1024
_MAX_FIELDS = 25


class EmbedColor(IntEnum):
    """Brand-consistent embed accent colors."""

    DEFAULT = 0x5865F2  # blurple
    SUCCESS = 0x2ECC71
    WARNING = 0xF1C40F
    ERROR = 0xE74C3C


class EmbedField(TypedDict):
    """A single embed field (Discord shape)."""

    name: str
    value: str
    inline: bool


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"  # ellipsis


def build_embed(
    title: str,
    description: str = "",
    *,
    color: EmbedColor = EmbedColor.DEFAULT,
    fields: list[EmbedField] | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    """Build a Discord embed dict.

    Values are truncated to Discord's documented limits so callers cannot emit
    an embed the API will reject.
    """
    embed: dict[str, Any] = {
        "title": _truncate(title, _TITLE_MAX),
        "color": int(color),
    }
    if description:
        embed["description"] = _truncate(description, _DESC_MAX)

    if fields:
        out: list[EmbedField] = []
        for f in fields[:_MAX_FIELDS]:
            out.append(
                {
                    "name": _truncate(f["name"], _FIELD_NAME_MAX),
                    "value": _truncate(f["value"], _FIELD_VALUE_MAX),
                    "inline": bool(f.get("inline", False)),
                }
            )
        embed["fields"] = out

    if footer:
        embed["footer"] = {"text": _truncate(footer, _FIELD_VALUE_MAX)}

    return embed


__all__ = ["EmbedColor", "EmbedField", "build_embed"]
