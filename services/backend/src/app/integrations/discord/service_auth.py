"""Service-auth boundary for the Discord bot bridge (PLAN.md §3.2).

THE TRUST BOUNDARY (read before touching the bridge)
----------------------------------------------------
The Discord bot has **no user JWT**. It authenticates to the backend with a single
shared secret — ``settings.bot_service_token`` — carried in the ``X-Service-Token``
header, and it *asserts* the Discord context of each interaction:

* ``discord_user_id`` — the invoking member's snowflake,
* ``discord_guild_id`` — the guild the slash command ran in,
* ``is_discord_guild_admin`` — whether that member holds Discord's **Manage-Guild**
  permission (derived from the interaction member by the bot).

The backend **TRUSTS** these assertions because the bot is the *only* holder of the
service token: a caller able to present the token is, by construction, the bot, and
the bot is trusted to report the gateway-provided member context faithfully. The
backend then maps ``discord_user_id`` → :class:`~app.models.User`, ``discord_guild_id``
→ :class:`~app.models.Guild`, sets the tenant GUC, and gates guild-admin actions on
``is_discord_guild_admin`` OR a persisted ``guild_admin`` role. A leaked token is
therefore a full compromise of the bot's authority — it is a service credential,
not a user credential, and must be protected accordingly.

The check itself mirrors :func:`app.lifecycle.router.require_service_token`
(constant-time compare, fail-closed/503 when unconfigured) but reads the token from
``app.config.Settings.bot_service_token`` rather than an env var directly, because
``app.config`` is owned by this phase's contract.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def require_service_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_service_token: Annotated[str | None, Header(alias="X-Service-Token")] = None,
) -> None:
    """Authorize the Discord bot via the shared service-token header.

    Compared in constant time. **Fails CLOSED**: if the backend has no
    ``bot_service_token`` configured the bridge is unusable (503 — "service auth
    not configured") rather than open; a missing/mismatched token is 401. Returns
    ``None`` on success (the dependency's only purpose is the side-effect gate).
    """
    expected = (settings.bot_service_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bot service auth not configured",
        )
    if not x_service_token or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid service token",
        )


__all__ = ["require_service_token"]
