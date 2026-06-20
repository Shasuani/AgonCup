"""Phase-0 smoke test: the bot constructs offline and exposes /ping.

Importing ``app.main`` must NOT connect to the gateway (PLAN.md §3.2). We assert
the bot is an AutoShardedBot, runs on the guilds-only intent, and has registered
the ``ping`` command — all without a token.
"""

from __future__ import annotations

from discord.ext import commands

from app.main import bot, list_command_names


def test_bot_is_autosharded() -> None:
    assert isinstance(bot, commands.AutoShardedBot)


def test_uses_only_guilds_intent() -> None:
    assert bot.intents.guilds is True
    # Privileged / heavy intents stay OFF (PLAN.md §9).
    assert bot.intents.members is False
    assert bot.intents.message_content is False


def test_ping_command_registered() -> None:
    assert "ping" in list_command_names()
