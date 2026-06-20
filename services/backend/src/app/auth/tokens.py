"""Rotating, revocable refresh tokens with reuse-detection (PLAN.md §3.1 auth).

A refresh token is an opaque, high-entropy secret handed to the client; the DB
stores only its SHA-256 hash (``RefreshToken.token_hash``), so a database leak
cannot replay sessions. On every ``/auth/refresh``:

* the presented secret is hashed and looked up;
* an **already-revoked** match means the token was *reused* (it was rotated away
  earlier) → the entire **family** is revoked and the refresh is rejected. This is
  the standard refresh-token-theft detection (rotation + reuse → kill family);
* a valid, unexpired, unrevoked token is **rotated**: it is marked revoked and a
  new token is issued in the same ``family_id`` with ``rotated_from_id`` set.

All functions take an ``AsyncSession`` so they participate in the caller's
transaction. These tables are GLOBAL (no guild RLS), so a plain session is fine.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import RefreshToken

# Bytes of entropy in the opaque refresh secret (256-bit).
_TOKEN_BYTES = 32


class RefreshTokenError(Exception):
    """Base class for refresh-token failures (always fail closed)."""


class InvalidRefreshToken(RefreshTokenError):
    """The presented token is unknown, expired, or structurally invalid."""


class RefreshTokenReuse(RefreshTokenError):
    """A revoked (already-rotated) token was presented → family revoked."""


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """The plaintext secret to hand to the client + its persisted row id."""

    token: str
    row_id: str
    family_id: str
    user_id: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(tz=UTC)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of an opaque token secret (what we persist)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_secret() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def issue_refresh_token(
    session: AsyncSession,
    *,
    user_id: str,
    family_id: str | None = None,
    rotated_from_id: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> IssuedRefreshToken:
    """Create + persist a new refresh token; return the plaintext secret.

    Used both for the initial login (``family_id``/``rotated_from_id`` omitted →
    new family root) and for rotation (caller threads the prior family + id).
    """
    settings = settings or get_settings()
    issued = now or _now()
    expires = issued + settings.jwt_refresh_ttl
    secret = _new_secret()

    row = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(secret),
        family_id=family_id or user_id,  # placeholder; replaced below when None
        issued_at=issued,
        expires_at=expires,
        revoked=False,
        rotated_from_id=rotated_from_id,
    )
    # A brand-new family uses the row's own id as the family id, so first flush to
    # obtain the server-generated id, then backfill family_id when not rotating.
    session.add(row)
    await session.flush()
    if family_id is None:
        row.family_id = row.id
        await session.flush()

    return IssuedRefreshToken(
        token=secret,
        row_id=str(row.id),
        family_id=str(row.family_id),
        user_id=str(row.user_id),
        expires_at=row.expires_at,
    )


async def _revoke_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        update(RefreshToken).where(RefreshToken.family_id == family_id).values(revoked=True)
    )


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    token: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> IssuedRefreshToken:
    """Validate + rotate a presented refresh token.

    Returns a freshly-issued token in the same family. Raises:
        RefreshTokenReuse: token was already revoked (reuse) — family is revoked.
        InvalidRefreshToken: unknown / expired token.
    """
    settings = settings or get_settings()
    moment = now or _now()
    token_hash = hash_token(token)

    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    if row is None:
        raise InvalidRefreshToken("unknown refresh token")

    if row.revoked:
        # Reuse of a rotated token → theft. Burn the whole family.
        await _revoke_family(session, str(row.family_id))
        raise RefreshTokenReuse("refresh token reuse detected; family revoked")

    if row.expires_at <= moment:
        row.revoked = True
        raise InvalidRefreshToken("refresh token expired")

    # Rotate: revoke the current row, issue its successor in the same family.
    row.revoked = True
    await session.flush()
    return await issue_refresh_token(
        session,
        user_id=str(row.user_id),
        family_id=str(row.family_id),
        rotated_from_id=str(row.id),
        settings=settings,
        now=moment,
    )


async def revoke_refresh_token(session: AsyncSession, *, token: str) -> bool:
    """Revoke a single presented refresh token (logout). Returns True if found.

    Idempotent: revoking an unknown or already-revoked token is a no-op success.
    """
    token_hash = hash_token(token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        return False
    row.revoked = True
    return True


async def revoke_family(session: AsyncSession, *, family_id: str) -> None:
    """Revoke every token in a family (e.g. global sign-out / account deletion)."""
    await _revoke_family(session, family_id)
