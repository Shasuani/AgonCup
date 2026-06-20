"""Guild onboarding handler tests (PLAN.md §3.2 multi-tenant onboarding).

Offline by design: the backend client is a stub. We assert that ``on_guild_join``
onboarding delegates to the bridge with the guild owner as the admin actor, and
that backend failures are swallowed (never crash the gateway).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.main import onboard_guild


class _FakeGuild:
    def __init__(self, guild_id: int, *, owner_id: int, name: str) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.name = name


class _RecordingBot:
    def __init__(self, backend: Any) -> None:
        self.backend = backend


class _OkBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def onboard_guild(self, ctx: Any, *, name: str) -> dict[str, Any]:
        self.calls.append({"ctx": ctx, "name": name})
        return {"guild_id": "g", "discord_guild_id": ctx.discord_guild_id}


class _FailBackend:
    async def onboard_guild(self, ctx: Any, *, name: str) -> dict[str, Any]:
        raise RuntimeError("backend down")


def test_onboard_delegates_with_owner_admin_context() -> None:
    backend = _OkBackend()
    bot = _RecordingBot(backend)
    guild = _FakeGuild(222, owner_id=99, name="My Guild")

    ok = asyncio.run(onboard_guild(bot, guild))  # type: ignore[arg-type]

    assert ok is True
    call = backend.calls[0]
    assert call["name"] == "My Guild"
    assert call["ctx"].discord_user_id == 99
    assert call["ctx"].discord_guild_id == 222
    assert call["ctx"].is_discord_guild_admin is True


def test_onboard_swallows_backend_errors() -> None:
    bot = _RecordingBot(_FailBackend())
    guild = _FakeGuild(1, owner_id=2, name="X")

    ok = asyncio.run(onboard_guild(bot, guild))  # type: ignore[arg-type]
    assert ok is False
