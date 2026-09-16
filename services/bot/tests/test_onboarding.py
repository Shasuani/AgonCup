"""Guild onboarding handler tests (PLAN.md §3.2 multi-tenant onboarding).

Offline by design: the backend client is a stub. We assert that ``on_guild_join``
onboarding delegates to the bridge with the guild owner as the admin actor —
under the owner's OWN display name, never the server's (ToDoList #7) — and that
backend failures are swallowed (never crash the gateway).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.main import onboard_guild, owner_display_name


class _FakeMember:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


class _FakeGuild:
    """A ``discord.Guild`` stand-in: ``get_member`` is the (guilds-intent) cache,
    ``fetch_member`` the REST fallback; either can be absent or fail."""

    def __init__(
        self,
        guild_id: int,
        *,
        owner_id: int,
        name: str,
        cached_owner: str | None = None,
        fetched_owner: str | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.name = name
        self._cached_owner = cached_owner
        self._fetched_owner = fetched_owner
        self._fetch_error = fetch_error
        self.fetched: list[int] = []

    def get_member(self, user_id: int) -> _FakeMember | None:
        if user_id == self.owner_id and self._cached_owner is not None:
            return _FakeMember(self._cached_owner)
        return None

    async def fetch_member(self, user_id: int) -> _FakeMember:
        self.fetched.append(user_id)
        if self._fetch_error is not None:
            raise self._fetch_error
        if user_id == self.owner_id and self._fetched_owner is not None:
            return _FakeMember(self._fetched_owner)
        raise LookupError(f"no member {user_id}")


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
    guild = _FakeGuild(222, owner_id=99, name="My Guild", cached_owner="Kostas")

    ok = asyncio.run(onboard_guild(bot, guild))  # type: ignore[arg-type]

    assert ok is True
    call = backend.calls[0]
    assert call["name"] == "My Guild"
    assert call["ctx"].discord_user_id == 99
    assert call["ctx"].discord_guild_id == 222
    assert call["ctx"].is_discord_guild_admin is True
    # The OWNER's name rides along as display_name — not the server's (ToDo #7).
    assert call["ctx"].display_name == "Kostas"
    assert guild.fetched == []  # cached: no REST round-trip


def test_onboard_fetches_owner_when_not_cached() -> None:
    """Under the guilds-only intent the owner is rarely cached: one REST fetch."""
    backend = _OkBackend()
    guild = _FakeGuild(222, owner_id=99, name="My Guild", fetched_owner="Kostas")

    asyncio.run(onboard_guild(_RecordingBot(backend), guild))  # type: ignore[arg-type]

    assert guild.fetched == [99]
    assert backend.calls[0]["ctx"].display_name == "Kostas"


def test_onboard_never_uses_the_server_name_as_the_owner_name() -> None:
    """Owner lookup failed -> display_name None (backend keeps the stored name);
    the old behaviour sent ``guild.name`` and renamed the owner "My Guild"."""
    backend = _OkBackend()
    guild = _FakeGuild(222, owner_id=99, name="My Guild", fetch_error=RuntimeError("discord down"))

    ok = asyncio.run(onboard_guild(_RecordingBot(backend), guild))  # type: ignore[arg-type]

    assert ok is True  # a name lookup failure never blocks onboarding
    assert backend.calls[0]["ctx"].display_name is None
    assert backend.calls[0]["ctx"].display_name != "My Guild"


def test_owner_display_name_without_owner_is_none() -> None:
    guild = _FakeGuild(1, owner_id=0, name="X", cached_owner="ignored")
    assert asyncio.run(owner_display_name(guild)) is None  # type: ignore[arg-type]


def test_onboard_swallows_backend_errors() -> None:
    bot = _RecordingBot(_FailBackend())
    guild = _FakeGuild(1, owner_id=2, name="X")

    ok = asyncio.run(onboard_guild(bot, guild))  # type: ignore[arg-type]
    assert ok is False
