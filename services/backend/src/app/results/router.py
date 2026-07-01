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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from lol_schemas.enums import ResultDecidedBy, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit_service
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

    # Append the privileged decision to the audit trail (best-effort; never fails
    # the record). Admin overrides are the primary thing the audit viewer surfaces.
    is_override = body.decided_by == ResultDecidedBy.ADMIN_OVERRIDE
    await audit_service.record_audit(
        session,
        action="result.override" if is_override else "result.decided",
        entity_type="match",
        entity_id=match_id,
        actor_id=admin_id,
        actor_kind=audit_service.ACTOR_GUILD,
        guild_id=result.guild_id,
        detail=body.reason,
    )
    return _to_response(result)


# ---------------------------------------------------------------------------
# Match history (read model) — per-game scoreboards for the SPA
# ---------------------------------------------------------------------------
class HistoryPlayerResponse(BaseModel):
    puuid: str
    display_name: str | None = None
    riot_id: str | None = None
    champion_id: int
    kills: int
    deaths: int
    assists: int


class HistoryTeamResponse(BaseModel):
    is_winner: bool
    players: list[HistoryPlayerResponse]


class HistoryGameResponse(BaseModel):
    match_id: str
    tournament_id: str
    tournament_name: str | None = None
    round: int | None = None
    lcu_game_id: str
    played_at: str
    teams: list[HistoryTeamResponse]


def _history_response(game: service.HistoryGame) -> HistoryGameResponse:
    return HistoryGameResponse(
        match_id=game.match_id,
        tournament_id=game.tournament_id,
        tournament_name=game.tournament_name,
        round=game.round,
        lcu_game_id=game.lcu_game_id,
        played_at=game.played_at.isoformat(),
        teams=[
            HistoryTeamResponse(
                is_winner=t.is_winner,
                players=[
                    HistoryPlayerResponse(
                        puuid=p.puuid,
                        display_name=p.display_name,
                        riot_id=p.riot_id,
                        champion_id=p.champion_id,
                        kills=p.kills,
                        deaths=p.deaths,
                        assists=p.assists,
                    )
                    for p in t.players
                ],
            )
            for t in game.teams
        ],
    )


@router.get(
    "/history",
    response_model=list[HistoryGameResponse],
    summary="Per-game match-history scoreboards for the acting guild (guild_admin)",
)
async def list_match_history(
    session: Annotated[AsyncSession, Depends(_ADMIN)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[HistoryGameResponse]:
    """Return recent per-game scoreboards (champion + KDA) for the acting guild.

    Display-only and uncorroborated (PLAN.md §3.6.5): reconstructed from the
    forgeable ``result_reports``; the official winner still comes from adjudication
    / admin override. RLS-scoped to the acting guild via the tenant session.
    """
    games = await service.list_match_history(session, limit=limit)
    return [_history_response(g) for g in games]


# ---------------------------------------------------------------------------
# Dispute / override queue — frozen matches awaiting an admin decision
# ---------------------------------------------------------------------------
class DisputeTeamResponse(BaseModel):
    team_id: str
    name: str


class DisputeAnomalyResponse(BaseModel):
    code: str
    detail: str
    severity: str | None = None


class DisputedMatchResponse(BaseModel):
    match_id: str
    tournament_id: str
    tournament_name: str | None = None
    round_label: str | None = None
    status: str
    teams: list[DisputeTeamResponse]
    anomalies: list[DisputeAnomalyResponse]


def _dispute_response(d: service.DisputeSummary) -> DisputedMatchResponse:
    return DisputedMatchResponse(
        match_id=d.match_id,
        tournament_id=d.tournament_id,
        tournament_name=d.tournament_name,
        round_label=d.round_label,
        status=d.status,
        teams=[DisputeTeamResponse(team_id=t.team_id, name=t.name) for t in d.teams],
        anomalies=[
            DisputeAnomalyResponse(code=flag, detail=flag.replace("_", " "), severity="high")
            for flag in d.anomaly_flags
        ],
    )


@router.get(
    "/disputes",
    response_model=list[DisputedMatchResponse],
    summary="Matches frozen as disputed, awaiting an admin override (guild_admin)",
)
async def list_disputes(
    session: Annotated[AsyncSession, Depends(_ADMIN)],
) -> list[DisputedMatchResponse]:
    """Return the acting guild's ``DISPUTED`` matches for the override queue.

    A match lands here when corroboration finds an adversarial signal (conflicting
    winner claims, forged payloads, ...) and freezes rather than auto-advancing.
    The admin resolves each via ``POST /results/matches/{id}/winner``. RLS-scoped.
    """
    disputes = await service.list_disputes(session)
    return [_dispute_response(d) for d in disputes]
