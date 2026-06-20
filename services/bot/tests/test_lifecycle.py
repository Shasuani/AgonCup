"""Bot guild-removal handler tests (PLAN.md §3.2 multi-tenant, §7 Phase 1 PII).

Offline by design:

* Importing ``app.main`` / ``app.lifecycle`` performs NO network I/O (Phase-0
  invariant — the gateway is never connected on import).
* ``app.lifecycle.purge_guild`` POSTs to the backend service-auth endpoint; httpx
  is fully MOCKED, so no live network is touched. We assert the URL, the
  ``X-Service-Token`` header, and the JSON body, and that errors are swallowed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import app.lifecycle as lifecycle
from app.main import bot, build_bot


# ---------------------------------------------------------------------------
# Import-safety
# ---------------------------------------------------------------------------
def test_import_is_offline() -> None:
    # Constructing the bot wires on_guild_remove without connecting the gateway.
    assert build_bot() is not None
    assert "on_guild_remove" in bot.extra_events or hasattr(bot, "on_guild_remove")


# ---------------------------------------------------------------------------
# Mocked httpx client
# ---------------------------------------------------------------------------
class _MockResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _MockAsyncClient:
    """Records the single POST so the test can assert URL/headers/body."""

    last_post: dict[str, Any] = {}

    def __init__(self, *args: Any, status_code: int = 200, **kwargs: Any) -> None:
        self._status_code = status_code

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        type(self).last_post = {"url": url, **kwargs}
        return _MockResponse(self._status_code)


def test_purge_guild_calls_backend_with_service_auth(monkeypatch: Any) -> None:
    monkeypatch.setenv(lifecycle.SERVICE_TOKEN_ENV, "shh-secret")
    monkeypatch.setenv("BACKEND_BASE_URL", "http://backend:8000")
    lifecycle.get_settings.cache_clear()
    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", _MockAsyncClient)

    ok = asyncio.run(lifecycle.purge_guild(123456789012345678))

    assert ok is True
    sent = _MockAsyncClient.last_post
    assert sent["url"].endswith(lifecycle.GUILD_REMOVE_PATH)
    assert sent["headers"]["X-Service-Token"] == "shh-secret"
    assert sent["json"] == {"discord_guild_id": 123456789012345678}


def test_purge_guild_skips_when_no_service_token(monkeypatch: Any) -> None:
    monkeypatch.delenv(lifecycle.SERVICE_TOKEN_ENV, raising=False)

    called = False

    class _ShouldNotBeUsed:
        def __init__(self, *a: Any, **k: Any) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", _ShouldNotBeUsed)
    ok = asyncio.run(lifecycle.purge_guild(42))

    assert ok is False
    assert called is False


def test_purge_guild_swallows_http_errors(monkeypatch: Any) -> None:
    monkeypatch.setenv(lifecycle.SERVICE_TOKEN_ENV, "tok")
    lifecycle.get_settings.cache_clear()

    def _raise(*a: Any, **k: Any) -> _MockAsyncClient:
        return _MockAsyncClient(status_code=500)

    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", _raise)
    ok = asyncio.run(lifecycle.purge_guild(7))
    # 500 → rejected → False, but no exception bubbles up.
    assert ok is False
