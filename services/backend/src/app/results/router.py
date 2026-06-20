"""Result-report ingress + decided-result recording (PLAN.md §3.6.6 / §7).

The forgeable per-participant report ingress + quorum/anomaly adjudication land
in later phases; this phase exposes the **decided-result** endpoint: an
authenticated ``guild_admin`` records a match's winner (an admin override, or an
already-adjudicated quorum/walkover/forfeit outcome). Recording upserts the
``MatchResult`` (UNIQUE(match_id)), stamps the ``Match``
(``winner_team_id`` + ``status = completed``), and advances the winner into the
next round's placeholder — all on the tenant session (RLS-scoped to the acting
guild). See :mod:`app.results.service`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from lol_schemas.enums import ResultDecidedBy, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Principal, get_current_principal, require_tenant_role
from app.models import MatchResult
from app.results import service

router = APIRouter(prefix="/results", tags=["results"])

# Recording a decided result (admin override / adjudicated outcome) is a
# privileged action in the acting guild (PLAN.md §3.6.6 admin override).
_ADMIN = require_tenant_role(UserGuildRoleName.GUILD_ADMIN)

# Provenances a decided-result POST may declare. Forfeit/walkover/quorum cover an
# already-adjudicated outcome being committed; admin_override is the default.
_ALLOWED_DECIDED_BY = frozenset(
    {
        ResultDecidedBy.ADMIN_OVERRIDE,
        ResultDecidedBy.QUORUM,
        ResultDecidedBy.WALKOVER,
        ResultDecidedBy.FORFEIT,
    }
)


@router.get("/_ping", summary="results module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted."""
    return {"module": "results", "status": "ok"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class RecordWinnerRequest(BaseModel):
    winner_team_id: uuid.UUID
    decided_by: ResultDecidedBy = ResultDecidedBy.ADMIN_OVERRIDE
    reason: str | None = Field(default=None, max_length=500)


class MatchResultResponse(BaseModel):
    match_id: str
    tournament_id: str
    winner_team_id: str
    decided_by: str
    state: str


def _to_response(result: MatchResult) -> MatchResultResponse:
    return MatchResultResponse(
        match_id=str(result.match_id),
        tournament_id=str(result.tournament_id),
        winner_team_id=str(result.winner_team_id),
        decided_by=result.decided_by.value if result.decided_by else "",
        state=result.state.value if hasattr(result.state, "value") else str(result.state),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/matches/{match_id}/winner",
    response_model=MatchResultResponse,
    summary="Record a match's decided winner and advance the bracket (guild_admin)",
)
async def record_match_winner(
    match_id: uuid.UUID,
    body: RecordWinnerRequest,
    session: Annotated[AsyncSession, Depends(_ADMIN)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> MatchResultResponse:
    """Record the decided winner of ``match_id`` and advance them (idempotent).

    Upserts the ``MatchResult``, stamps the ``Match``, and conditionally advances
    the winner into the next round (PLAN.md §3.6.6 / §2). Re-recording the same
    winner is a no-op idempotent update. ``404`` if the match is not in the acting
    guild; ``409`` if the match is not ready (missing a team) or the declared
    winner is not a participant.
    """
    if body.decided_by not in _ALLOWED_DECIDED_BY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"unsupported decided_by {body.decided_by}"
        )
    try:
        admin_id = uuid.UUID(principal.user_id) if principal.user_id else None
    except (ValueError, TypeError):
        admin_id = None
    try:
        result = await service.record_winner(
            session,
            match_id=match_id,
            winner_team_id=body.winner_team_id,
            decided_by=body.decided_by,
            admin_override_by=admin_id,
            admin_override_reason=body.reason,
        )
    except service.MatchNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="match not found") from exc
    except service.ResultAlreadyDecidedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except service.ResultConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (service.MatchNotReadyError, service.TeamNotInMatchError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(result)
