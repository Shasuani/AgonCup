"""Guild-removal lifecycle handler (PLAN.md §3.2 multi-tenant, §7 Phase 1 PII).

When the bot is removed from a Discord server, the backend must purge that guild's
scoped data (``GUILD_REMOVE`` → retention-windowed deletion, PLAN.md §3.2). This
module owns the small backend client + the ``on_guild_remove`` callback so
``app.main`` stays a thin wiring layer.

Offline/test-safe: importing this module performs **no** network I/O. The HTTP
client is created lazily inside :func:`purge_guild`, and the gateway callback is
only invoked by discord.py at runtime.

Service auth: the call is authenticated with a shared secret (the
``X-Service-Token`` header), read from the :data:`SERVICE_TOKEN_ENV` environment
variable — the backend validates the same value. There is no user JWT here (the
bot has no user context).
"""

from __future__ import annotations

import logging
import os

import httpx

from app.config import get_settings

#: Env var holding the shared service-auth secret (must match the backend's
#: ``LIFECYCLE_SERVICE_TOKEN``). Read directly so no config field is required.
SERVICE_TOKEN_ENV = "LIFECYCLE_SERVICE_TOKEN"

#: Backend route that purges a guild by its Discord snowflake (service-auth).
GUILD_REMOVE_PATH = "/api/v1/lifecycle/internal/guild-remove"

_HTTP_TIMEOUT_SECONDS = 10.0

log = logging.getLogger("app.bot.lifecycle")


async def purge_guild(discord_guild_id: int) -> bool:
    """Tell the backend to purge a guild the bot was removed from.

    POSTs the Discord snowflake to the service-auth purge endpoint. Returns
    ``True`` on a 2xx response. Network/HTTP errors are logged and swallowed
    (return ``False``) — a failed purge must not crash the gateway event loop; the
    backend retention sweep is the backstop (PLAN.md §3.2).
    """
    settings = get_settings()
    token = os.environ.get(SERVICE_TOKEN_ENV, "").strip()
    if not token:
        log.warning("guild_remove purge skipped: %s not set", SERVICE_TOKEN_ENV)
        return False

    url = f"{settings.backend_base_url.rstrip('/')}{GUILD_REMOVE_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json={"discord_guild_id": discord_guild_id},
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError as exc:
        log.warning("guild_remove purge request failed (guild=%s): %s", discord_guild_id, exc)
        return False

    if response.status_code >= 400:
        log.warning(
            "guild_remove purge rejected (guild=%s): status=%s",
            discord_guild_id,
            response.status_code,
        )
        return False

    log.info("guild_remove purge ok (guild=%s)", discord_guild_id)
    return True
