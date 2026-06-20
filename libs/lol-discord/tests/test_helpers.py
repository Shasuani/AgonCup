"""Tests for the embed builder + error formatter."""

from __future__ import annotations

from lol_discord import EmbedColor, build_embed, format_error


def test_build_embed_basic_shape() -> None:
    embed = build_embed("Hello", "World", color=EmbedColor.SUCCESS)
    assert embed["title"] == "Hello"
    assert embed["description"] == "World"
    assert embed["color"] == int(EmbedColor.SUCCESS)
    assert "fields" not in embed


def test_build_embed_truncates_and_caps_fields() -> None:
    long_title = "x" * 1000
    fields = [{"name": "n", "value": "v", "inline": True} for _ in range(40)]
    embed = build_embed(long_title, fields=fields)
    assert len(embed["title"]) == 256
    assert len(embed["fields"]) == 25
    assert embed["fields"][0]["inline"] is True


def test_build_embed_footer() -> None:
    embed = build_embed("t", footer="made by lol")
    assert embed["footer"]["text"] == "made by lol"


def test_format_error_from_exception() -> None:
    out = format_error(ValueError("bad input"))
    assert out == "Error: bad input"


def test_format_error_neutralizes_mentions_and_collapses() -> None:
    out = format_error("hey @everyone\nand <@123>", prefix="Oops")
    assert "@everyone" not in out
    assert "<@123>" not in out
    assert "\n" not in out
    assert out.startswith("Oops: ")


def test_format_error_empty_exception_uses_class_name() -> None:
    out = format_error(RuntimeError())
    assert out == "Error: RuntimeError"
