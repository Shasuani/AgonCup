"""Typed httpx client for the backend Discord bridge (PLAN.md §3.2).

The bot is a **thin presentation layer**: it holds no user JWT and contains no
game logic. Every command handler delegates to this client, which calls the
backend's service-authenticated ``/integrations/discord`` bridge.

Trust boundary (PLAN.md §3.2, mirrored in the backend's ``service_auth``): the
bot is the SOLE holder of ``BOT_SERVICE_TOKEN`` and presents it in the
``X-Service-Token`` header. The backend therefore trusts the Discord context the
bot ASSERTS on every call — ``discord_user_id``, ``discord_guild_id`` and
``is_discord_guild_admin`` (derived from the interaction member's Manage-Guild
permission) — and maps it to a ``User``/``Guild`` + RLS tenant on its side.

Offline/test-safe: importing this module performs NO network I/O. The client
opens a short-lived :class:`httpx.AsyncClient` per call (matching the lifecycle
helper), so there is no shared connection to construct on import. Tests mock
``httpx.AsyncClient`` and assert the header + JSON body of each request.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.config import get_settings

#: Header carrying the shared service-auth secret (PLAN.md §3.2). Matches the
#: backend's ``app.integrations.discord.service_auth.require_service_token``.
SERVICE_TOKEN_HEADER = "X-Service-Token"

#: Prefix every bridge route shares (mounted under ``/api/v1`` on the backend).
_BRIDGE_PREFIX = "/api/v1/integrations/discord"

_HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class DiscordContext:
    """The Discord interaction context the bot asserts on every bridge call.

    Carried as the ``ctx`` object in each request body (PLAN.md §3.2). The
    backend trusts these values because only the bot holds the service token.
    ``is_discord_guild_admin`` is the invoking member's **Manage-Guild**
    permission, read from the interaction by the cogs.
    """

    discord_user_id: int
    discord_guild_id: int
    is_discord_guild_admin: bool = False
    display_name: str | None = None


class BackendError(Exception):
    """A bridge call returned a non-2xx response.

    Carries the HTTP ``status_code`` and the backend ``detail`` (when present) so
    cogs can surface a clean, user-facing message without leaking internals.
    """

    def __init__(self, status_code: int, detail: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail or "backend request failed"
        super().__init__(f"backend error {status_code}: {self.detail}")


class BackendClient:
    """Typed wrapper over the backend Discord bridge (one method per endpoint).

    Construction is side-effect-free (it only reads settings); no network is
    touched until a method is awaited. The service token is sent on every
    request, so a missing token surfaces as a backend 401/503 rather than a
    silent no-op.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        timeout: float = _HTTP_TIMEOUT_SECONDS,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.backend_base_url).rstrip("/")
        self._service_token = (
            service_token if service_token is not None else settings.bot_service_token
        )
        self._timeout = timeout

    # -- transport ----------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self._base_url}{_BRIDGE_PREFIX}{path}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST ``payload`` to a bridge route with service auth; return the JSON body.

        Raises :class:`BackendError` on any non-2xx response, parsing the backend's
        ``detail`` for a clean message. Returns ``None`` for a 204 (e.g. team leave).
        """
        headers = {SERVICE_TOKEN_HEADER: self._service_token}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url(path), json=payload, headers=headers)

        if response.status_code == httpx.codes.NO_CONTENT:
            return None
        if response.status_code >= 400:
            raise BackendError(response.status_code, _detail(response))
        body = response.json()
        return body if isinstance(body, dict) else {"data": body}

    async def _post_list(self, path: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """POST and return a JSON list body (for the list endpoints)."""
        headers = {SERVICE_TOKEN_HEADER: self._service_token}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url(path), json=payload, headers=headers)

        if response.status_code >= 400:
            raise BackendError(response.status_code, _detail(response))
        body = response.json()
        return list(body) if isinstance(body, list) else []

    # -- onboarding / settings ---------------------------------------------
    async def onboard_guild(
        self,
        ctx: DiscordContext,
        *,
        name: str,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upsert the guild + settings on ``GUILD_JOIN`` (PLAN.md §3.2 onboarding)."""
        payload: dict[str, Any] = {"ctx": _ctx_payload(ctx), "name": name}
        if settings is not None:
            payload["settings"] = settings
        return await self._require(self._post("/guilds/onboard", payload))

    async def get_guild_settings(self, ctx: DiscordContext) -> dict[str, Any]:
        """Read the guild's bot settings (notify channel, admin roles, feed flag)."""
        return await self._require(self._post("/guilds/settings/get", {"ctx": _ctx_payload(ctx)}))

    async def resolve_guild(self, ctx: DiscordContext) -> dict[str, Any]:
        """Read-only resolve: backend guild_id + settings for a Discord guild.

        Used by the notification router to map origin_guild_id -> channel without
        the mutating onboard upsert.
        """
        return await self._require(self._post("/guilds/resolve", {"ctx": _ctx_payload(ctx)}))

    async def update_guild_settings(
        self, ctx: DiscordContext, *, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Patch the guild's bot settings (guild admin; PLAN.md §3.2)."""
        payload = {"ctx": _ctx_payload(ctx), "patch": patch}
        return await self._require(self._post("/guilds/settings/update", payload))

    # -- accounts -----------------------------------------------------------
    async def start_registration(self, ctx: DiscordContext) -> dict[str, Any]:
        """Mint a one-time desktop-verification code for the acting user (PLAN.md §3.5).

        The backend bridge (``/accounts/link``, now code-minting) delegates to
        ``app.accounts.service.start_desktop_registration`` and returns
        ``{attempt_id, code, expires_at, origin_guild_id, instruction}``. The bot
        only relays the asserted Discord context (no Riot ID / region) — the
        desktop app reads the player's PUUID from the local League client (LCU)
        and completes the link with this code.
        """
        return await self._require(self._post("/accounts/link", {"ctx": _ctx_payload(ctx)}))

    #: Back-compat alias for callers still referring to the old method name. The
    #: signature is the new parameter-less one (the icon/Riot-ID flow is gone).
    link_account = start_registration

    async def list_my_accounts(self, ctx: DiscordContext) -> list[dict[str, Any]]:
        """List the acting user's own linked Riot accounts (self-service ``/accounts``).

        The backend bridge (``/accounts/list``) returns the user's own rows only —
        each ``{account_id, game_name, tag_line, region, verified, rank_text}``.
        """
        return await self._post_list("/accounts/list", {"ctx": _ctx_payload(ctx)})

    async def unlink_account(
        self, ctx: DiscordContext, *, game_name: str, tag_line: str
    ) -> dict[str, Any]:
        """Remove one of the acting user's own linked accounts by Riot ID (``/unlink``).

        Delegates to the bridge ``/accounts/unlink``; a Riot ID the user has not
        linked surfaces a clean ``BackendError`` (404).
        """
        payload = {"ctx": _ctx_payload(ctx), "game_name": game_name, "tag_line": tag_line}
        return await self._require(self._post("/accounts/unlink", payload))

    # -- teams --------------------------------------------------------------
    async def create_team(self, ctx: DiscordContext, *, name: str) -> dict[str, Any]:
        """Create a team owned by the acting user (PLAN.md §2 Team)."""
        payload = {"ctx": _ctx_payload(ctx), "name": name}
        return await self._require(self._post("/teams/create", payload))

    async def join_team(self, ctx: DiscordContext, *, team: str) -> dict[str, Any]:
        """Join an existing team by NAME or id (whatever the player typed)."""
        payload = {"ctx": _ctx_payload(ctx), "team": team}
        return await self._require(self._post("/teams/join", payload))

    async def leave_team(self, ctx: DiscordContext, *, team_id: str) -> None:
        """Leave a team (backend returns 204)."""
        await self._post("/teams/leave", {"ctx": _ctx_payload(ctx), "team_id": team_id})

    # -- tournaments --------------------------------------------------------
    async def list_tournaments(self, ctx: DiscordContext) -> list[dict[str, Any]]:
        """List the guild's tournaments (PLAN.md §2 Tournament)."""
        return await self._post_list("/tournaments/list", {"ctx": _ctx_payload(ctx)})

    async def create_tournament(
        self,
        ctx: DiscordContext,
        *,
        name: str,
        max_teams: int = 16,
        game_mode: str = "summoners_rift_5v5",
        best_of: int = 1,
    ) -> dict[str, Any]:
        """Create a tournament (guild admin). ``game_mode`` = 5v5 or 1v1 ARAM; ``best_of`` 1/3/5."""
        payload = {
            "ctx": _ctx_payload(ctx),
            "name": name,
            "max_teams": max_teams,
            "game_mode": game_mode,
            "best_of": best_of,
        }
        return await self._require(self._post("/tournaments/create", payload))

    async def join_tournament(
        self, ctx: DiscordContext, *, tournament_id: str, team_id: str
    ) -> dict[str, Any]:
        """Register a team into a tournament (PLAN.md §2 Registration)."""
        payload = {
            "ctx": _ctx_payload(ctx),
            "tournament_id": tournament_id,
            "team_id": team_id,
        }
        return await self._require(self._post("/tournaments/join", payload))

    async def leave_tournament(
        self, ctx: DiscordContext, *, tournament_id: str, team_id: str
    ) -> None:
        """Withdraw a team from a tournament (PLAN.md §2 Registration).

        Mirrors the backend bridge ``leave_tournament`` function. The route returns
        204; a backend that has not yet mounted it surfaces a clean ``BackendError``.
        """
        payload = {
            "ctx": _ctx_payload(ctx),
            "tournament_id": tournament_id,
            "team_id": team_id,
        }
        await self._post("/tournaments/leave", payload)

    async def tournament_status(self, ctx: DiscordContext, *, tournament_id: str) -> dict[str, Any]:
        """Get a tournament's current status (PLAN.md §2 TournamentStatus)."""
        payload = {"ctx": _ctx_payload(ctx), "tournament_id": tournament_id}
        return await self._require(self._post("/tournaments/status", payload))

    # -- helpers ------------------------------------------------------------
    @staticmethod
    async def _require(coro: Awaitable[dict[str, Any] | None]) -> dict[str, Any]:
        """Await a ``_post`` and assert a JSON body (non-204 endpoints)."""
        body = await coro
        if body is None:
            raise BackendError(502, "backend returned empty body")
        return body


def _ctx_payload(ctx: DiscordContext) -> dict[str, Any]:
    """Serialise the Discord context into the bridge ``ctx`` object."""
    return asdict(ctx)


def _detail(response: httpx.Response) -> str | None:
    """Best-effort extraction of the backend's error ``detail`` string."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return None


__all__ = [
    "SERVICE_TOKEN_HEADER",
    "BackendClient",
    "BackendError",
    "DiscordContext",
]
