"""Discord OAuth2 (authorization-code + ``state``) client (PLAN.md §3.1 auth).

Discord is the **sole identity provider** (PLAN.md §11 decision 6). This module
builds the authorize URL, exchanges the auth code for tokens, and fetches the
Discord user — all the external-HTTP surface of login lives here so the router
stays thin and the tests can mock a single seam (``httpx.AsyncClient``).

PKCE-ready (desktop client)
---------------------------
The web flow uses the confidential-client ``client_secret``. The desktop client
is a *public* client and must use PKCE instead: it generates a
``code_verifier``/``code_challenge`` (S256), sends ``code_challenge`` +
``code_challenge_method=S256`` on the authorize URL, and ``code_verifier`` on the
token exchange. :func:`build_authorize_url` and :func:`exchange_code` already
accept the optional ``code_challenge``/``code_verifier`` parameters so the desktop
flow can be added without changing this module's shape.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"
DISCORD_USER_GUILDS_URL = "https://discord.com/api/users/@me/guilds"

#: Scopes we request at login. ``guilds`` is READ-ONLY (it lists the servers the
#: user is in — it cannot join or modify anything) and is what powers the
#: "pick a server to install the bot into" screen: without it ``/users/@me/guilds``
#: 403s and we cannot know where the user is an admin. No ``email``/``guilds.join``.
DEFAULT_SCOPES = ("identify", "guilds")

#: Scopes on the BOT-invite URL — a separate flow from user login. ``bot`` is what
#: makes Discord render the server picker; ``applications.commands`` registers the
#: slash commands.
BOT_INVITE_SCOPES = ("bot", "applications.commands")

#: Discord ``ADMINISTRATOR`` permission bit (1 << 3). A user may only install the
#: bot into a guild they administer, so this is the filter for the picker.
PERMISSION_ADMINISTRATOR = 0x8


class DiscordOAuthError(Exception):
    """A Discord OAuth exchange or user-fetch failed."""


class DiscordReauthRequired(DiscordOAuthError):
    """The stored Discord token is expired, revoked, or missing the needed scope.

    Raised (rather than a generic failure) so the API can tell the SPA to send the
    user back through ``/auth/login`` — the case that hits every account which
    logged in before ``guilds`` joined :data:`DEFAULT_SCOPES`.
    """


@dataclass(frozen=True, slots=True)
class DiscordTokens:
    access_token: str
    refresh_token: str | None
    scopes: tuple[str, ...]
    expires_in: int | None


@dataclass(frozen=True, slots=True)
class DiscordUser:
    discord_user_id: int
    username: str


@dataclass(frozen=True, slots=True)
class DiscordGuild:
    """One entry of ``/users/@me/guilds`` — a server the logged-in user is in."""

    discord_guild_id: int
    name: str
    icon: str | None
    owner: bool
    permissions: int

    @property
    def is_admin(self) -> bool:
        """True when the user owns the guild or holds ``ADMINISTRATOR`` in it.

        Owners implicitly hold every permission, but Discord still reports the
        bit for them; checking ``owner`` too keeps this correct if that changes.
        """
        return self.owner or bool(self.permissions & PERMISSION_ADMINISTRATOR)


def generate_state() -> str:
    """A high-entropy anti-CSRF ``state`` value to round-trip through the IdP."""
    return secrets.token_urlsafe(32)


def build_authorize_url(
    *,
    state: str,
    settings: Settings | None = None,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    code_challenge: str | None = None,
) -> str:
    """Build the Discord authorize URL. Pass ``code_challenge`` for PKCE (desktop)."""
    settings = settings or get_settings()
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        # `none` skips Discord's authorization screen for a user who has already
        # granted these scopes — they land straight back on the callback. The
        # value was `consent`, which means "ask EVERY time", so signing in showed
        # the Authorize button on every single login. A first-time user (or one
        # who revoked access, or a login that asks for a scope they have not
        # granted) still gets the screen: `none` only suppresses a re-consent that
        # would be answered identically.
        "prompt": "none",
    }
    if code_challenge is not None:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    code: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient,
    code_verifier: str | None = None,
) -> DiscordTokens:
    """Exchange an auth ``code`` for tokens. Pass ``code_verifier`` for PKCE."""
    settings = settings or get_settings()
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.discord_redirect_uri,
    }
    if code_verifier is not None:
        data["code_verifier"] = code_verifier

    resp = await client.post(
        DISCORD_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != httpx.codes.OK:
        raise DiscordOAuthError(f"token exchange failed: {resp.status_code}")
    payload = resp.json()
    access = payload.get("access_token")
    if not access:
        raise DiscordOAuthError("token response missing access_token")
    raw_scope = payload.get("scope", "")
    scopes = tuple(s for s in raw_scope.split(" ") if s)
    return DiscordTokens(
        access_token=access,
        refresh_token=payload.get("refresh_token"),
        scopes=scopes,
        expires_in=payload.get("expires_in"),
    )


async def fetch_user(
    access_token: str,
    *,
    client: httpx.AsyncClient,
) -> DiscordUser:
    """Fetch the authenticated Discord user (``/users/@me``)."""
    resp = await client.get(
        DISCORD_USER_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != httpx.codes.OK:
        raise DiscordOAuthError(f"user fetch failed: {resp.status_code}")
    payload = resp.json()
    raw_id = payload.get("id")
    if raw_id is None:
        raise DiscordOAuthError("user response missing id")
    return DiscordUser(
        discord_user_id=int(raw_id),
        username=payload.get("global_name") or payload.get("username") or str(raw_id),
    )


async def fetch_user_guilds(
    access_token: str,
    *,
    client: httpx.AsyncClient,
) -> tuple[DiscordGuild, ...]:
    """Fetch the servers the user belongs to (``/users/@me/guilds``).

    Requires the ``guilds`` scope. A 401/403 means the stored token is expired,
    revoked, or predates that scope — surfaced as :class:`DiscordReauthRequired`
    so the caller can bounce the user through login again rather than showing a
    generic failure.
    """
    resp = await client.get(
        DISCORD_USER_GUILDS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        raise DiscordReauthRequired(f"guild list denied: {resp.status_code}")
    if resp.status_code != httpx.codes.OK:
        raise DiscordOAuthError(f"guild list failed: {resp.status_code}")

    guilds: list[DiscordGuild] = []
    for entry in resp.json():
        # Login now calls this to sync guild_admin roles, so an unexpected payload
        # shape must degrade to "no guilds", never raise into the login path.
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id")
        if raw_id is None:
            continue
        guilds.append(
            DiscordGuild(
                discord_guild_id=int(raw_id),
                name=entry.get("name") or str(raw_id),
                icon=entry.get("icon"),
                owner=bool(entry.get("owner", False)),
                # Discord sends the bitfield as a STRING (it exceeds 2^53).
                permissions=int(entry.get("permissions") or 0),
            )
        )
    return tuple(guilds)


def build_bot_invite_url(
    *,
    settings: Settings | None = None,
    guild_id: int | None = None,
) -> str:
    """Build the "add the bot to a server" URL (a bot install, NOT a user login).

    Discord renders its own server picker on this page, listing only guilds the
    user administers — the install itself is authorised by Discord, not by us.
    Passing ``guild_id`` preselects one server and locks the picker so the user
    lands on the exact server they clicked in our UI.
    """
    settings = settings or get_settings()
    params = {
        "client_id": settings.discord_client_id,
        "scope": " ".join(BOT_INVITE_SCOPES),
        "permissions": str(settings.discord_bot_permissions),
    }
    if guild_id is not None:
        params["guild_id"] = str(guild_id)
        params["disable_guild_select"] = "true"
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"
