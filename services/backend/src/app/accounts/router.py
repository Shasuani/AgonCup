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

import time
from collections import deque
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from lol_riot import RiotAccountClient
from pydantic import BaseModel, Field

from app.accounts import service
from app.auth.deps import Principal, get_current_principal
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.logging import get_logger
from app.notifications import NotificationPublisher, publish_registration_verified

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


# ---------------------------------------------------------------------------
# Unauthenticated-endpoint rate limit (code-guessing defence)
# ---------------------------------------------------------------------------
#: Per-IP fixed window: at most this many verify attempts per ``_RL_WINDOW_S``.
#: TODO(scale): this is process-local; move to Redis when the backend runs >1
#: replica so the window is shared (in-memory gives ZERO cross-replica
#: protection). Adequate as a single-node guess-rate brake.
#:
#: PROXY STORY: the key is the socket peer (``request.client.host``). This is
#: correct only single-hop (direct client connections). Behind a reverse proxy
#: every request shares the proxy IP, collapsing all clients into one bucket
#: (over-throttling). We deliberately DO NOT trust ``X-Forwarded-For`` here —
#: it is client-spoofable and would let an attacker bypass the limiter by
#: rotating a forged header. If a trusted proxy is deployed, switch to a
#: proxy-set header validated against a known proxy allowlist before keying on
#: it. Until then, keep socket-peer keying.
_RL_MAX = 10
_RL_WINDOW_S = 60.0
_rl_hits: dict[str, deque[float]] = {}


def _rate_limit_ok(key: str, *, now: float | None = None) -> bool:
    """Fixed-window per-key limiter. Returns False when the window is exhausted.

    Fully-decayed keys are pruned so an IP-rotating attacker cannot grow
    ``_rl_hits`` without bound (slow memory leak): each call sweeps stale keys
    opportunistically, and the requesting key is dropped if it ends up empty.
    """
    now = now if now is not None else time.monotonic()
    # Opportunistic sweep of stale keys (bounds the dict under IP rotation).
    stale = [k for k, h in _rl_hits.items() if not h or now - h[-1] > _RL_WINDOW_S]
    for k in stale:
        del _rl_hits[k]
    hits = _rl_hits.setdefault(key, deque())
    while hits and now - hits[0] > _RL_WINDOW_S:
        hits.popleft()
    if len(hits) >= _RL_MAX:
        return False
    hits.append(now)
    return True


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
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
    summary="Complete account linking from the desktop app using a one-time code",
)
async def verify_desktop(
    body: DesktopVerifyRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    publisher: Annotated[NotificationPublisher, Depends(get_notification_publisher)],
) -> DesktopVerifyResponse:
    """Resolve the canonical PUUID for the code's user, dedupe-link it, DM the user.

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
    if not _rate_limit_ok(client_ip):
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
