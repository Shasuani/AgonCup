"""The browser's session cookie: a signed JWT wrapping the refresh token.

WHY A COOKIE
------------
The admin SPA used to keep its refresh token in React state only, so every page
reload dropped the session and sent the user back through Discord. Persisting it
in ``localStorage`` would fix that but hand the long-lived credential to any
injected script; an ``HttpOnly`` cookie is unreadable from JavaScript, so an XSS
can act while the page is open but cannot *exfiltrate* the session and replay it
later from elsewhere.

WHY A JWT INSIDE IT
-------------------
The cookie's value is a JWT signed with the same ``Settings.jwt_secret`` as the
access token, carrying the opaque refresh token as a claim. The signature makes
the cookie tamper-evident and the ``exp`` claim gives it an expiry the server
enforces cryptographically — a forged, mangled, or stale cookie is rejected
before any database work. The refresh token inside stays the real credential:
rotation and reuse-detection (``app.auth.tokens``) are unchanged, so revocation
still happens in the database and logout genuinely ends the session.

``typ`` is distinct from the access token's, so neither can ever be replayed as
the other even though both are signed with the same key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from jwt import InvalidTokenError

from app.config import Settings, get_settings

#: Cookie name. The ``__Host-`` prefix is a browser-enforced guarantee: the
#: cookie is only accepted over HTTPS, with Path=/ and NO Domain attribute, so a
#: sibling subdomain cannot set or overwrite it. Dropped in dev, where the SPA is
#: served over plain http and the prefix would make the cookie unsettable.
SECURE_COOKIE_NAME = "__Host-lol_session"
DEV_COOKIE_NAME = "lol_session"

SESSION_TOKEN_TYPE = "session"  # noqa: S105 - a claim value, not a secret


class InvalidSessionCookie(Exception):
    """The cookie is absent, expired, tampered with, or not a session token."""


@dataclass(frozen=True, slots=True)
class SessionCookieClaims:
    """The decoded, validated payload of a session cookie."""

    user_id: str
    refresh_token: str
    expires_at: datetime


def cookie_name(settings: Settings | None = None) -> str:
    """``__Host-``-prefixed name in production, plain name in local development."""
    settings = settings or get_settings()
    return SECURE_COOKIE_NAME if settings.is_production else DEV_COOKIE_NAME


def mint_session_cookie(
    *,
    user_id: str,
    refresh_token: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """Sign a session JWT carrying ``refresh_token``.

    Its lifetime deliberately matches the refresh token's: a cookie that outlived
    the token would send the browser through a refresh that can only ever 401.
    """
    settings = settings or get_settings()
    issued = now or datetime.now(tz=UTC)
    expires = issued + settings.jwt_refresh_ttl
    payload = {
        "sub": user_id,
        "rt": refresh_token,
        "typ": SESSION_TOKEN_TYPE,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def read_session_cookie(
    raw: str | None, *, settings: Settings | None = None
) -> SessionCookieClaims:
    """Verify + decode a session cookie.

    Raises:
        InvalidSessionCookie: missing, expired, badly signed, wrong token type, or
            structurally invalid. Never leaks which — the caller answers 401 for
            all of them alike.
    """
    if not raw:
        raise InvalidSessionCookie("no session cookie")
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            raw,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except InvalidTokenError as exc:
        raise InvalidSessionCookie(str(exc)) from exc

    if payload.get("typ") != SESSION_TOKEN_TYPE:
        # An access JWT presented as a session cookie (or vice versa): same key,
        # different purpose, so the type claim is what keeps them apart.
        raise InvalidSessionCookie("not a session token")

    user_id = payload.get("sub")
    refresh_token = payload.get("rt")
    if not isinstance(user_id, str) or not isinstance(refresh_token, str) or not refresh_token:
        raise InvalidSessionCookie("malformed session token")

    return SessionCookieClaims(
        user_id=user_id,
        refresh_token=refresh_token,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )
