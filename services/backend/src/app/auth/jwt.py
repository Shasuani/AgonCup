"""Short-lived access-JWT minting + verification (PLAN.md §3.1 auth).

Access tokens are stateless HS256 JWTs signed with ``Settings.jwt_secret``. They
are deliberately short-lived (~15m, ``Settings.jwt_access_ttl``); long-lived
*refresh* tokens (in ``app.auth.tokens``) are what get rotated/revoked.

Claims
------
* ``sub``  — the user id (UUID string).
* ``gid``  — the *acting guild* id this token is scoped to (the tenant the request
             will run under). May be ``None`` for a guild-less session (e.g. right
             after login, before a guild is selected).
* ``roles``— the user's role *values* in the acting guild (RBAC, see
             ``app.auth.deps``). Empty when ``gid`` is None.
* ``exp`` / ``iat`` — standard expiry/issued-at (UTC).
* ``typ`` — ``"access"`` so a refresh token can never be replayed as an access JWT.

Verification rejects expired and tampered tokens (``InvalidAccessToken``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from jwt import InvalidTokenError

from app.config import Settings, get_settings

ACCESS_TOKEN_TYPE = "access"


class InvalidAccessToken(Exception):
    """Raised when an access JWT is expired, tampered, or otherwise invalid."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """The decoded, validated payload of an access JWT."""

    user_id: str
    guild_id: str | None
    roles: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(tz=UTC)


def mint_access_token(
    *,
    user_id: str,
    guild_id: str | None,
    roles: tuple[str, ...] | list[str],
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """Mint a signed, short-lived access JWT for ``user_id`` in ``guild_id``."""
    settings = settings or get_settings()
    issued = now or _now()
    expires = issued + settings.jwt_access_ttl
    payload = {
        "sub": user_id,
        "gid": guild_id,
        "roles": list(roles),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_access_token(token: str, *, settings: Settings | None = None) -> AccessClaims:
    """Verify + decode an access JWT.

    Raises:
        InvalidAccessToken: expired signature, bad signature, wrong token type,
            or a structurally invalid payload.
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except InvalidTokenError as exc:  # covers expiry, bad signature, malformed
        raise InvalidAccessToken(str(exc)) from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessToken("not an access token")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidAccessToken("missing subject")

    gid = payload.get("gid")
    if gid is not None and not isinstance(gid, str):
        raise InvalidAccessToken("invalid guild claim")

    raw_roles = payload.get("roles", [])
    if not isinstance(raw_roles, list) or not all(isinstance(r, str) for r in raw_roles):
        raise InvalidAccessToken("invalid roles claim")

    return AccessClaims(
        user_id=sub,
        guild_id=gid,
        roles=tuple(raw_roles),
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
    )
