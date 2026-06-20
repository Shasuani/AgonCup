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

from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from lol_riot import RiotAccountClient
from pydantic import BaseModel, Field

from app.accounts import service
from app.auth.deps import Principal, get_current_principal
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.logging import get_logger

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
    puuid: str
    status: str
    target_icon_id: int
    expires_at: datetime
    verified: bool


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
