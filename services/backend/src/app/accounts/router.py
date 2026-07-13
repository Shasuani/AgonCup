"""Riot account linking + the hardened ownership-verification flow.

Endpoints (PLAN.md §3.1 accounts/, §3.5 Account-V1):

* ``POST /accounts/link`` — authenticated. Resolve a Riot ID → canonical PUUID
  via Account-V1, create/return an (unverified) ``RiotAccount`` and open a
  ``VerificationAttempt`` (random target profile-icon + single-use nonce + hard
  TTL). The response tells the user which icon to set; the worker poll
  (``account_verification_poll``) flips ``verified`` once it observes the icon.
* ``GET /accounts/verify/{attempt_id}/status`` — authenticated. Report the
  attempt's current status to the linking user.

``RiotAccount`` / ``VerificationAttempt`` are **global** (not guild-scoped,
PLAN.md §2), so these run on a plain ``SessionLocal`` (no tenant GUC) and scope
ownership to the authenticated principal in the query.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from lol_riot import RiotAccountClient
from lol_schemas.enums import UserGuildRoleName
from pydantic import BaseModel, Field

from app.accounts import service
from app.auth.deps import Principal, get_current_principal, require_role
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.logging import get_logger
from app.notifications import NotificationPublisher, publish_registration_verified
from app.ratelimit import FixedWindowLimiter

router = APIRouter(prefix="/accounts", tags=["accounts"])
log = get_logger("app.accounts.router")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class LinkRequest(BaseModel):
    """Link a Riot account: ``gameName``/``tagLine`` Riot ID + platform region."""

    game_name: str = Field(min_length=1, max_length=64, alias="gameName")
    tag_line: str = Field(min_length=1, max_length=16, alias="tagLine")
    region: str = Field(min_length=2, max_length=8)

    model_config = {"populate_by_name": True}


class LinkResponse(BaseModel):
    """The opened ownership challenge returned to the caller (PLAN.md §3.1).

    ``target_icon_id`` is the profile icon the user must set on their League
    client; ``attempt_id`` is polled via the status endpoint.
    """

    account_id: str
    puuid: str
    verified: bool
    attempt_id: str
    target_icon_id: int
    expires_at: datetime
    game_name: str
    tag_line: str


class AttemptStatusResponse(BaseModel):
    """A verification attempt's current state (PLAN.md §3.1 status reporting)."""

    attempt_id: str
    puuid: str | None
    status: str
    target_icon_id: int | None
    expires_at: datetime
    verified: bool


class RosterEntryResponse(BaseModel):
    """One linked player in the admin roster (PLAN.md §3.4).

    Maps :class:`app.accounts.service.LinkedAccountSummary` — the canonical Riot
    ID, region, verification state, and the owner's Discord display name.
    """

    account_id: str
    user_id: str
    discord_name: str
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    verified: bool
    verified_at: datetime | None = None
    origin_guild_id: str | None = None
    origin_guild_name: str | None = None


class DesktopVerifyRequest(BaseModel):
    """Desktop -> backend verify body. Mirrors ``lol_schemas.DesktopVerifyRequestDTO``.

    The ``code`` IS the credential (no JWT/service-token). The desktop reads the
    Riot ID (``game_name``/``tag_line``) + platform ``region`` from the local LCU;
    the backend resolves the CANONICAL PUUID server-side via Account-V1 (the
    desktop no longer sends it). ``lcu_puuid`` (advisory 36-char LCU UUID) and
    ``summoner_id`` (LCU invites) are optional secondary signals.
    """

    code: str = Field(min_length=8, max_length=12)
    game_name: str = Field(min_length=1, max_length=64)
    tag_line: str = Field(min_length=1, max_length=16)
    region: str = Field(min_length=2, max_length=8)
    lcu_puuid: str | None = Field(default=None, max_length=128)
    summoner_id: str | None = Field(default=None, max_length=64)


class DesktopVerifyResponse(BaseModel):
    """Desktop verify result. Mirrors ``lol_schemas.DesktopVerifyResponseDTO``."""

    account_id: str
    puuid: str
    verified: bool
    origin_guild_id: str | None = None
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    tier: str | None = None
    division: str | None = None
    league_points: int | None = None
    rank_text: str = "Unranked"


class VerifyLcuRequest(BaseModel):
    """JWT-authed LCU verify body. Mirrors ``lol_schemas.VerifyLcuRequestDTO``.

    The session is already established (``/auth/desktop-pair``), so there is NO
    ``code`` here — the bearer JWT identifies the user. The desktop reads the Riot
    ID + region from the local LCU; the backend resolves the canonical PUUID via
    Account-V1 (the double verification). ``lcu_puuid``/``summoner_id`` are advisory.
    """

    game_name: str = Field(min_length=1, max_length=64)
    tag_line: str = Field(min_length=1, max_length=16)
    region: str = Field(min_length=2, max_length=8)
    lcu_puuid: str | None = Field(default=None, max_length=128)
    summoner_id: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Unauthenticated-endpoint rate limit (code-guessing defence)
# ---------------------------------------------------------------------------
#: Per-IP fixed window for ``/accounts/verify-desktop`` (10/60s on the socket
#: peer). Factored into :mod:`app.ratelimit` and SHARED in spirit with the auth
#: router's pairing/refresh limiters; see that module for the scale/proxy caveats.
_verify_limiter = FixedWindowLimiter(max_hits=10, window_s=60.0)
#: Per-PRINCIPAL fixed window for the JWT-authed ``/accounts/verify-lcu`` (6/60s on
#: ``principal.user_id``) — a gentle brake on retry storms / Riot-budget abuse.
_verify_lcu_limiter = FixedWindowLimiter(max_hits=6, window_s=60.0)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
class VerificationEnqueuer(Protocol):
    """Enqueues the worker ``account_verification_poll`` job for an attempt.

    The backend does not depend on arq directly (the worker owns the broker
    client); the concrete enqueuer is injected at app wiring time. The default
    is a log-only stub so the link flow is fully functional standalone and the
    real arq pool wiring is an additive follow-up (PLAN.md §1.1 worker tier).
    """

    async def __call__(self, attempt_id: str) -> None: ...


async def _log_only_enqueuer(attempt_id: str) -> None:
    log.info("verification_poll_enqueue_requested", attempt_id=attempt_id)


def get_verification_enqueuer() -> VerificationEnqueuer:
    """FastAPI dependency yielding the verification-poll enqueuer (overridable)."""
    return _log_only_enqueuer


def get_notification_publisher() -> NotificationPublisher:
    """FastAPI dependency yielding the internal-push notification publisher.

    Returns the production :class:`RedisNotificationPublisher` wired onto the
    process-wide Redis pool (mirrors ``app.orchestration.router._command_publisher``).
    Tests override this with a :class:`RecordingNotificationPublisher`, and the
    base :class:`NotificationPublisher` (log-only) keeps the path importable offline.
    Deferred imports keep the accounts module's load light + IO-free.
    """
    from app.notifications.service import RedisNotificationPublisher  # noqa: PLC0415
    from app.realtime.redis_client import get_redis  # noqa: PLC0415

    return RedisNotificationPublisher(get_redis())


class MyAccountResponse(BaseModel):
    """One of the authenticated user's OWN linked Riot accounts (``GET /accounts/me``).

    The self-service counterpart of :class:`RosterEntryResponse` (which is the
    admin, all-users roster): scoped to the bearer, no Discord/cross-user join.
    Mirrors :class:`app.accounts.service.MyAccountSummary`.
    """

    account_id: str
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    verified: bool
    rank_text: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/me",
    response_model=list[MyAccountResponse],
    summary="List the authenticated user's own linked Riot accounts",
)
async def my_accounts(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[MyAccountResponse]:
    """Return every Riot account the authenticated bearer has linked (self-service).

    The JWT-authed read the desktop client and SPA use to show "your accounts" and
    offer "link another". ``RiotAccount`` is GLOBAL (not guild-RLS), so this runs on
    a plain ``SessionLocal`` and scopes ownership to the principal in the query.
    """
    async with SessionLocal() as session:
        rows = await service.list_user_accounts(session, user_id=principal.user_id)
    return [
        MyAccountResponse(
            account_id=a.account_id,
            game_name=a.game_name,
            tag_line=a.tag_line,
            region=a.region,
            verified=a.verified,
            rank_text=a.rank_text,
        )
        for a in rows
    ]


@router.delete(
    "/me/{account_id}",
    response_model=MyAccountResponse,
    summary="Unlink (unregister) one of the authenticated user's own Riot accounts",
)
async def unlink_my_account(
    account_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> MyAccountResponse:
    """Remove one of the caller's own linked accounts by id (the "unregister" button).

    Self-service: scoped to the bearer, so a principal can only delete their own
    account. ``RiotAccount`` is GLOBAL (not guild-RLS), so this runs on a plain
    ``SessionLocal``; deleting the row is referentially safe (history keyed on the
    PUUID string survives) and the player can re-link later.
    """
    try:
        uuid.UUID(account_id)  # malformed id → 404 (never a raw DB cast error/500)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not linked"
        ) from exc
    try:
        async with SessionLocal() as session, session.begin():
            removed = await service.unlink_account_by_id(
                session, user_id=principal.user_id, account_id=account_id
            )
    except service.AccountNotLinked as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not linked"
        ) from exc
    return MyAccountResponse(
        account_id=removed.account_id,
        game_name=removed.game_name,
        tag_line=removed.tag_line,
        region=removed.region,
        verified=removed.verified,
        rank_text=removed.rank_text,
    )


@router.get(
    "",
    response_model=list[RosterEntryResponse],
    summary="ADMIN: list everyone who has linked a Riot account",
)
async def list_accounts(
    _principal: Annotated[
        Principal,
        Depends(require_role(UserGuildRoleName.GUILD_ADMIN, UserGuildRoleName.PLATFORM_OPERATOR)),
    ],
) -> list[RosterEntryResponse]:
    """Return the platform-wide player roster (staff only).

    ``RiotAccount`` is a GLOBAL table (not guild-RLS), so this runs on a plain
    ``SessionLocal`` and returns every linked account; the ``require_role`` guard
    restricts it to guild admins / platform operators (PLAN.md §3.4).
    """
    async with SessionLocal() as session:
        rows = await service.list_linked_accounts(session)
    return [
        RosterEntryResponse(
            account_id=r.account_id,
            user_id=r.user_id,
            discord_name=r.discord_name,
            game_name=r.game_name,
            tag_line=r.tag_line,
            region=r.region,
            verified=r.verified,
            verified_at=r.verified_at,
            origin_guild_id=r.origin_guild_id,
            origin_guild_name=r.origin_guild_name,
        )
        for r in rows
    ]


@router.post(
    "/link",
    response_model=LinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a Riot account (Account-V1) and open an ownership challenge",
)
async def link(
    body: LinkRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
    enqueue: Annotated[VerificationEnqueuer, Depends(get_verification_enqueuer)],
) -> LinkResponse:
    """Resolve the Riot ID, open a verification attempt, enqueue the worker poll.

    PUUID is canonical (PLAN.md §2); the response carries the random target icon
    the user must set to prove ownership of the resolved account.
    """
    # Route Account-V1 by the player's region (continental cluster, PLAN.md §3.5).
    try:
        platform = service.parse_platform_region(body.region)
    except service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    client = RiotAccountClient(settings.riot_api_key, service.continent_for(platform))
    try:
        async with SessionLocal() as session, session.begin():
            result = await service.link_account(
                session,
                client,
                user_id=principal.user_id,
                game_name=body.game_name,
                tag_line=body.tag_line,
                region=body.region,
            )
    except service.UnknownRiotId as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except service.RiotUnavailable as exc:
        log.warning("account_v1_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Riot Account-V1 unavailable; try again",
        ) from exc
    finally:
        await client.aclose()

    # Hand the ownership poll to the worker tier (PLAN.md §1.1) AFTER commit.
    await enqueue(result.attempt_id)

    return LinkResponse(
        account_id=result.account_id,
        puuid=result.puuid,
        verified=result.verified,
        attempt_id=result.attempt_id,
        target_icon_id=result.target_icon_id,
        expires_at=result.expires_at,
        game_name=result.game_name,
        tag_line=result.tag_line,
    )


@router.get(
    "/verify/{attempt_id}/status",
    response_model=AttemptStatusResponse,
    summary="Report a verification attempt's status",
)
async def verify_status(
    attempt_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AttemptStatusResponse:
    """Return the status of an attempt owned by the authenticated principal."""
    async with SessionLocal() as session:
        attempt = await service.get_attempt(
            session, attempt_id=attempt_id, user_id=principal.user_id
        )
    if attempt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="verification attempt not found"
        )

    return AttemptStatusResponse(
        attempt_id=str(attempt.id),
        puuid=attempt.puuid,
        status=attempt.status,
        target_icon_id=attempt.target_icon_id,
        expires_at=attempt.expires_at,
        verified=attempt.status == service.STATUS_VERIFIED,
    )


# A cheap shape gate so obviously-malformed codes 422 before any DB lookup and
# don't burn a rate-limit slot on the DB. The service re-normalises canonically.
_CODE_CHARSET = set(service._CODE_ALPHABET)


@router.post(
    "/verify-desktop",
    response_model=DesktopVerifyResponse,
    summary="DEPRECATED: complete account linking from the desktop using a one-time code",
    deprecated=True,
)
async def verify_desktop(
    body: DesktopVerifyRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    publisher: Annotated[NotificationPublisher, Depends(get_notification_publisher)],
) -> DesktopVerifyResponse:
    """DEPRECATED — superseded by ``/auth/desktop-pair`` + ``/accounts/verify-lcu``.

    The desktop now (1) exchanges its one-time code for a SESSION via
    ``POST /auth/desktop-pair`` then (2) links its League account authenticated via
    ``POST /accounts/verify-lcu`` (JWT bearer, no code). This combined code+link
    endpoint is retained UNCHANGED for ONE release after the new desktop ships, then
    deleted along with :class:`DesktopVerifyRequest` and
    :func:`app.accounts.service.verify_desktop_code`.

    Resolve the canonical PUUID for the code's user, dedupe-link it, DM the user.

    UNAUTHENTICATED by design: the one-time ``code`` (issued to an authenticated
    Discord user inside ``/register``, 15-min TTL, single-use, superseded on
    re-register) IS the credential — there is no JWT or service token here. A
    per-IP fixed-window rate limit guards against code-guessing and failed lookups
    return a generic 404 (no "unknown" vs "expired" distinction).

    DOUBLE VERIFICATION (PLAN.md §3.1): the code proves Discord-registration
    control; resolving the Riot ID -> canonical PUUID via Account-V1 proves the
    Riot ID is real (a 404 rejects). The backend resolves the PUUID server-side —
    the desktop no longer sends it (which also fixes the duplicate-row bug). See
    ``app.accounts.service.verify_desktop_code``.
    """
    # Cheap shape validation before any state touch (also pre-rate-limit).
    normalized = service.normalize_code(body.code)
    if len(normalized) != service._CODE_LEN or not set(normalized) <= _CODE_CHARSET:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed code"
        )

    client_ip = request.client.host if request.client else "unknown"
    if not _verify_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts"
        )

    # Validate the region up front so Account-V1 is routed by the right continent
    # (mirrors /link). 422 before any Riot call or state touch.
    try:
        platform = service.parse_platform_region(body.region)
    except service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    account_client = RiotAccountClient(settings.riot_api_key, service.continent_for(platform))
    try:
        # NOTE: ``verify_desktop_code`` OWNS its own short transactions (it must not
        # hold a Postgres row lock across the Riot HTTP calls), so the router does
        # NOT open ``session.begin()`` here — it just provides the session.
        async with SessionLocal() as session:
            result = await service.verify_desktop_code(
                session,
                code=body.code,
                game_name=body.game_name,
                tag_line=body.tag_line,
                region=body.region,
                account_client=account_client,
                lcu_puuid=body.lcu_puuid,
                summoner_id=body.summoner_id,
            )
    except service.UnknownOrExpiredCode as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="invalid or expired code"
        ) from exc
    except service.UnknownRiotId as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Riot ID not found"
        ) from exc
    except service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except service.PuuidConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this Riot account is already linked to another user",
        ) from exc
    except service.RiotUnavailable as exc:
        log.warning("account_v1_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Riot Account-V1 unavailable; try again",
        ) from exc
    finally:
        await account_client.aclose()

    # DM the verifying user (best-effort) AFTER commit (mirror /link's enqueue).
    # DM-routed, so the payload carries discord_user_id (the documented exception
    # to the no-identifier notification rule). Only emit when we know the guild +
    # snowflake; a missing snowflake (no User row) is logged, not fatal.
    if result.origin_guild_id and result.discord_user_id:
        # Best-effort: a notification-transport error (Redis down) must NEVER
        # convert an already-committed, successful verification into a 500. The
        # link is durable; the DM is advisory. ``rank_text`` carries only the
        # snapshot ("Gold II, 45 LP" / "Unranked"); the embed's "Solo/Duo" field
        # name supplies the label, so we must NOT prefix it here (double-label).
        try:
            await publish_registration_verified(
                publisher,
                origin_guild_id=result.origin_guild_id,
                discord_user_id=result.discord_user_id,
                riot_id=f"{result.game_name}#{result.tag_line}",
                region=service.region_label_for(platform),
                rank_text=result.rank_text,
            )
        except Exception as exc:  # noqa: BLE001 - notifications are best-effort
            log.warning(
                "registration_verified_publish_failed",
                error=str(exc),
                origin_guild_id=result.origin_guild_id,
            )
    else:
        log.info(
            "registration_verified_notify_skipped",
            has_guild=bool(result.origin_guild_id),
            has_user=bool(result.discord_user_id),
        )

    return DesktopVerifyResponse(
        account_id=result.account_id,
        puuid=result.puuid,
        verified=result.verified,
        origin_guild_id=result.origin_guild_id,
        game_name=result.game_name,
        tag_line=result.tag_line,
        region=result.region,
        tier=result.tier,
        division=result.division,
        league_points=result.league_points,
        rank_text=result.rank_text,
    )


@router.post(
    "/verify-lcu",
    response_model=DesktopVerifyResponse,
    summary="Link a League account from the (already-paired) desktop app (JWT-authed)",
)
async def verify_lcu(
    body: VerifyLcuRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
    publisher: Annotated[NotificationPublisher, Depends(get_notification_publisher)],
) -> DesktopVerifyResponse:
    """Resolve the canonical PUUID for the BEARER user, dedupe-link it, DM the user.

    The JWT-authed replacement for ``/accounts/verify-desktop`` (PLAN.md §3.1): the
    desktop has already paired (``/auth/desktop-pair``) into a session, so the USER
    is the authenticated principal and there is NO one-time code. The desktop reads
    the Riot ID + region from the local LCU; the backend resolves the canonical
    78-char PUUID server-side via Account-V1 by-riot-id (the double verification —
    a 404 rejects), dedupes the ``RiotAccount`` on that PUUID (409 if it belongs to
    another user), snapshots a best-effort Solo/Duo rank, and DMs the user.
    """
    try:
        platform = service.parse_platform_region(body.region)
    except service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if not _verify_lcu_limiter.allow(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many attempts"
        )

    account_client = RiotAccountClient(settings.riot_api_key, service.continent_for(platform))
    try:
        # The service OWNS its own short transaction (it must not hold a Postgres
        # row lock across the Riot HTTP calls), so the router does NOT open
        # ``session.begin()`` here — it just provides the session.
        async with SessionLocal() as session:
            result = await service.verify_lcu_account(
                session,
                user_id=principal.user_id,
                game_name=body.game_name,
                tag_line=body.tag_line,
                region=body.region,
                account_client=account_client,
                lcu_puuid=body.lcu_puuid,
                summoner_id=body.summoner_id,
                origin_guild_id=principal.guild_id,
            )
    except service.UnknownRiotId as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Riot ID not found"
        ) from exc
    except service.InvalidRegion as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except service.PuuidConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this Riot account is already linked to another user",
        ) from exc
    except service.RiotUnavailable as exc:
        log.warning("account_v1_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Riot Account-V1 unavailable; try again",
        ) from exc
    finally:
        await account_client.aclose()

    # Best-effort DM the verifying user AFTER commit (mirrors verify-desktop). Only
    # when the principal carries an acting guild + we resolved the snowflake; a
    # notification-transport error must NEVER fail an already-committed link.
    if result.origin_guild_id and result.discord_user_id:
        try:
            await publish_registration_verified(
                publisher,
                origin_guild_id=result.origin_guild_id,
                discord_user_id=result.discord_user_id,
                riot_id=f"{result.game_name}#{result.tag_line}",
                region=service.region_label_for(platform),
                rank_text=result.rank_text,
            )
        except Exception as exc:  # noqa: BLE001 - notifications are best-effort
            log.warning(
                "registration_verified_publish_failed",
                error=str(exc),
                origin_guild_id=result.origin_guild_id,
            )
    else:
        log.info(
            "registration_verified_notify_skipped",
            has_guild=bool(result.origin_guild_id),
            has_user=bool(result.discord_user_id),
        )

    return DesktopVerifyResponse(
        account_id=result.account_id,
        puuid=result.puuid,
        verified=result.verified,
        origin_guild_id=result.origin_guild_id,
        game_name=result.game_name,
        tag_line=result.tag_line,
        region=result.region,
        tier=result.tier,
        division=result.division,
        league_points=result.league_points,
        rank_text=result.rank_text,
    )
