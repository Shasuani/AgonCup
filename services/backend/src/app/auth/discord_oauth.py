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

# Scopes we request: identity only (no email/guilds.join etc.).
DEFAULT_SCOPES = ("identify",)


class DiscordOAuthError(Exception):
    """A Discord OAuth exchange or user-fetch failed."""


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
        "prompt": "consent",
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
