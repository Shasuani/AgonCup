"""Rotating, revocable refresh tokens with reuse-detection (PLAN.md §3.1 auth).

A refresh token is an opaque, high-entropy secret handed to the client; the DB
stores only its SHA-256 hash (``RefreshToken.token_hash``), so a database leak
cannot replay sessions. On every ``/auth/refresh``:

* the presented secret is hashed and looked up;
* an **already-revoked** match means the token was *reused* (it was rotated away
  earlier) → the entire **family** is revoked and the refresh is rejected. This is
  the standard refresh-token-theft detection (rotation + reuse → kill family).
  The one exception is a **rotation race** — the same token presented twice within
  ``Settings.refresh_rotation_grace`` while the earlier rotation's product is
  still live — which is what two tabs booting together, or a client retrying a
  request whose response it never saw, actually look like. Those get a sibling
  token instead of a dead session; see :func:`rotate_refresh_token`;
* a valid, unexpired, unrevoked token is **rotated**: it is marked revoked and a
  new token is issued in the same ``family_id`` with ``rotated_from_id`` set.

All functions take an ``AsyncSession`` so they participate in the caller's
transaction. These tables are GLOBAL (no guild RLS), so a plain session is fine.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import RefreshToken

# Bytes of entropy in the opaque refresh secret (256-bit).
_TOKEN_BYTES = 32

# A zero grace disables the rotation-race leeway entirely (strict reuse detection).
_ZERO = timedelta(0)


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
    device_label: str | None = None,
    device_id: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> IssuedRefreshToken:
    """Create + persist a new refresh token; return the plaintext secret.

    Used both for the initial login (``family_id``/``rotated_from_id`` omitted →
    new family root) and for rotation (caller threads the prior family + id). The
    optional ``device_label``/``device_id`` tag the token with the desktop device
    that paired (PLAN.md §3.1 desktop pairing); they are carried forward across
    rotation by :func:`rotate_refresh_token` so a device label survives the family.
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
        device_label=device_label,
        device_id=device_id,
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


async def _live_successor(
    session: AsyncSession, *, row_id: str, moment: datetime
) -> RefreshToken | None:
    """The newest still-usable token rotated directly off ``row_id``, if any.

    ``limit(1)``, not ``scalar_one_or_none``: a graced race (below) deliberately
    issues a SIBLING off the same predecessor, so one row can have several
    successors and a strict single-row fetch would explode on the second replay.
    """
    return (
        (
            await session.execute(
                select(RefreshToken)
                .where(
                    RefreshToken.rotated_from_id == row_id,
                    RefreshToken.revoked.is_(False),
                    RefreshToken.expires_at > moment,
                )
                .order_by(RefreshToken.issued_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
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
        # Not every replay is theft. Two tabs booting together, or a request the
        # client retried because it never saw the response, both present the same
        # token twice within a heartbeat of each other — and burning the family
        # there logs a legitimate user out of a session they just established.
        #
        # So: if this token was rotated moments ago AND that rotation's product is
        # still live, treat it as a race and issue a SIBLING off the same
        # predecessor. The earlier successor is deliberately LEFT ALIVE — revoking
        # it would just move the problem, since whichever response reaches the
        # browser last is the cookie it keeps, and half the time that is the other
        # one. Two live tokens for a few seconds is the price; genuine reuse
        # outside the window, or of a token whose chain has already moved on,
        # still burns the family.
        successor = await _live_successor(session, row_id=str(row.id), moment=moment)
        grace = settings.refresh_rotation_grace
        if (
            successor is not None
            and grace > _ZERO
            and moment - successor.issued_at <= grace
            and row.expires_at > moment
        ):
            return await issue_refresh_token(
                session,
                user_id=str(row.user_id),
                family_id=str(row.family_id),
                rotated_from_id=str(row.id),
                device_label=row.device_label,
                device_id=row.device_id,
                settings=settings,
                now=moment,
            )

        # Reuse of a rotated token → theft. Burn the whole family.
        await _revoke_family(session, str(row.family_id))
        raise RefreshTokenReuse("refresh token reuse detected; family revoked")

    if row.expires_at <= moment:
        row.revoked = True
        raise InvalidRefreshToken("refresh token expired")

    # Rotate: revoke the current row, issue its successor in the same family. The
    # predecessor's device metadata is CARRIED FORWARD so a paired device stays
    # labelled across the whole token family (PLAN.md §3.1 desktop pairing).
    row.revoked = True
    await session.flush()
    return await issue_refresh_token(
        session,
        user_id=str(row.user_id),
        family_id=str(row.family_id),
        rotated_from_id=str(row.id),
        device_label=row.device_label,
        device_id=row.device_id,
        settings=settings,
        now=moment,
    )


async def revoke_refresh_token(session: AsyncSession, *, token: str) -> bool:
    """End the session the presented token belongs to (logout). True if found.

    Revokes the whole **family**, not just the presented row. A family is exactly
    one login lineage — every login mints a fresh family (see ``_issue_pair``) —
    so this is what "log me out" means, and revoking one row is not: the
    rotation-race grace in :func:`rotate_refresh_token` can leave a live sibling,
    and a single-row revoke would sail straight past it and leave the session
    alive for whoever holds it.

    Idempotent: revoking an unknown or already-revoked token is a no-op success.
    """
    token_hash = hash_token(token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        return False
    await _revoke_family(session, str(row.family_id))
    return True


async def revoke_family(session: AsyncSession, *, family_id: str) -> None:
    """Revoke every token in a family (e.g. global sign-out / account deletion)."""
    await _revoke_family(session, family_id)


async def revoke_all_for_user(session: AsyncSession, *, user_id: str) -> None:
    """Revoke EVERY refresh token belonging to ``user_id`` (all families).

    The global sign-out / right-to-be-forgotten path: when a user is deleted
    (PLAN.md §7 Privacy/PII) we revoke their refresh tokens explicitly BEFORE the
    cascading ``users.id`` DELETE removes the rows, so any token presented in the
    narrow window between revocation and deletion fails closed.
    """
    await session.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )
