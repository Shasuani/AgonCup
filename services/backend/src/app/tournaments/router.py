"""Tournament CRUD, registration/check-in, and bracket start (PLAN.md §3.1).

Thin HTTP + authorization shell over :mod:`app.tournaments.service`. Every route
runs on the tenant session (RLS-scoped to the acting guild). Creating/starting a
tournament requires ``guild_admin``; registering/checking-in a team requires
``captain`` (or ``guild_admin``). The acting guild comes from the ``X-Guild-Id``
header or the JWT claim (these routes key on ``tournament_id``, not a path guild).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from lol_schemas.enums import GameMode, TournamentFormat, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    Principal,
    get_current_principal,
    get_tenant_session,
    require_tenant_role,
)
from app.models.tables import Registration, Tournament
from app.tournaments import service

router = APIRouter(prefix="/tournaments", tags=["tournaments"])

_ADMIN = require_tenant_role(UserGuildRoleName.GUILD_ADMIN)
_CAPTAIN = require_tenant_role(UserGuildRoleName.CAPTAIN, UserGuildRoleName.GUILD_ADMIN)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class CreateTournamentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    format: TournamentFormat = TournamentFormat.SINGLE_ELIMINATION
    game_mode: GameMode = GameMode.SUMMONERS_RIFT_5V5
    best_of: int = Field(default=1, description="Series length: 1, 3, or 5.")
    max_teams: int = Field(default=16, ge=2, le=16)
    ruleset: dict[str, Any] = Field(default_factory=dict)
    # Scheduling (UTC, both optional). When registration_opens_at is in the future
    # the tournament is created DRAFT (registration closed) and the scheduler opens
    # + announces it at that time; start_at is the scheduled kickoff (PLAN.md §3.2).
    registration_opens_at: datetime | None = None
    start_at: datetime | None = None


class TournamentResponse(BaseModel):
    id: str
    name: str
    status: str
    format: str
    game_mode: str
    best_of: int
    max_teams: int
    is_shared_to_central: bool
    registration_opens_at: str | None = None
    start_at: str | None = None


class CreatedTournamentResponse(TournamentResponse):
    """Create result — carries the ``join_code`` the admin hands to players.

    The code is returned ONLY here (and to the bot bridge on create), never in the
    list/get responses, so possession of the code stays the admin's gate on who
    can enrol (PLAN.md §3.1).
    """

    join_code: str


class JoinByCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    # Team-mode only: which captained team enrols. Omit when the caller captains a
    # single team (auto-resolved) or for solo modes (ignored).
    team_id: uuid.UUID | None = None


class TeamRef(BaseModel):
    team_id: uuid.UUID


class RegistrationResponse(BaseModel):
    id: str
    tournament_id: str
    team_id: str
    status: str


class StartResponse(BaseModel):
    tournament_id: str
    matches_created: int
    rounds: int
    byes: int


class BracketMatchResponse(BaseModel):
    round: int
    slot: int
    team_a_id: str | None
    team_a_name: str | None
    team_b_id: str | None
    team_b_name: str | None
    winner_team_id: str | None
    status: str


class BracketResponse(BaseModel):
    tournament: TournamentResponse
    matches: list[BracketMatchResponse]


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _to_response(t: Tournament) -> TournamentResponse:
    return TournamentResponse(
        id=str(t.id),
        name=t.name,
        status=_enum_value(t.status),
        format=_enum_value(t.format),
        game_mode=_enum_value(t.game_mode),
        best_of=t.best_of,
        max_teams=t.max_teams,
        is_shared_to_central=t.is_shared_to_central,
        registration_opens_at=(
            t.registration_opens_at.isoformat() if t.registration_opens_at else None
        ),
        start_at=t.start_at.isoformat() if t.start_at else None,
    )


def _reg_response(reg: Registration) -> RegistrationResponse:
    return RegistrationResponse(
        id=str(reg.id),
        tournament_id=str(reg.tournament_id),
        team_id=str(reg.team_id),
        status=_enum_value(reg.status),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "", response_model=CreatedTournamentResponse, summary="Create a tournament (guild_admin)"
)
async def create_tournament(
    body: CreateTournamentRequest,
    session: Annotated[AsyncSession, Depends(_ADMIN)],
) -> CreatedTournamentResponse:
    try:
        t = await service.create_tournament(
            session,
            name=body.name,
            format=body.format,
            game_mode=body.game_mode,
            best_of=body.best_of,
            max_teams=body.max_teams,
            ruleset=body.ruleset,
            registration_opens_at=body.registration_opens_at,
            start_at=body.start_at,
        )
    except service.InvalidStateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    base = _to_response(t)
    return CreatedTournamentResponse(**base.model_dump(), join_code=t.join_code or "")


@router.get("", response_model=list[TournamentResponse], summary="List tournaments in this guild")
async def list_tournaments(
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TournamentResponse]:
    return [_to_response(t) for t in await service.list_tournaments(session)]


@router.get(
    "/mine",
    response_model=list[TournamentResponse],
    summary="List tournaments the caller has enrolled in",
)
async def list_my_tournaments(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TournamentResponse]:
    rows = await service.list_my_tournaments(session, user_id=uuid.UUID(principal.user_id))
    return [_to_response(t) for t in rows]


@router.post(
    "/join-by-code",
    response_model=TournamentResponse,
    summary="Enrol the caller into a tournament by its join code",
)
async def join_by_code(
    body: JoinByCodeRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TournamentResponse:
    try:
        tournament, _ = await service.join_by_code(
            session,
            code=body.code,
            user_id=uuid.UUID(principal.user_id),
            team_id=body.team_id,
        )
    except service.InvalidJoinCodeError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invalid join code") from exc
    except service.RegistrationNotOpenError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"registration opens at {exc} (UTC)",
        ) from exc
    except service.RegistrationClosedError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="tournament is not accepting players"
        ) from exc
    except service.NoCaptainTeamError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="this is a team tournament — create a team first, then join as its captain",
        ) from exc
    except service.AmbiguousCaptainTeamError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="you captain multiple teams — specify which team_id joins",
        ) from exc
    except service.NotTeamCaptainError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="only the team captain can enrol the team",
        ) from exc
    except service.TournamentFullError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="tournament is full") from exc
    return _to_response(tournament)


@router.get("/{tournament_id}", response_model=TournamentResponse, summary="Get a tournament")
async def get_tournament(
    tournament_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TournamentResponse:
    try:
        return _to_response(await service.get_tournament(session, tournament_id=tournament_id))
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc


@router.get(
    "/{tournament_id}/bracket",
    response_model=BracketResponse,
    summary="The tournament's bracket (matches with team names)",
)
async def get_bracket(
    tournament_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> BracketResponse:
    try:
        tournament = await service.get_tournament(session, tournament_id=tournament_id)
        matches = await service.get_bracket(session, tournament_id=tournament_id)
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
    return BracketResponse(
        tournament=_to_response(tournament),
        matches=[
            BracketMatchResponse(
                round=m.round,
                slot=m.slot,
                team_a_id=str(m.team_a_id) if m.team_a_id else None,
                team_a_name=m.team_a_name,
                team_b_id=str(m.team_b_id) if m.team_b_id else None,
                team_b_name=m.team_b_name,
                winner_team_id=str(m.winner_team_id) if m.winner_team_id else None,
                status=m.status,
            )
            for m in matches
        ],
    )


@router.post(
    "/{tournament_id}/registrations",
    response_model=RegistrationResponse,
    summary="Register a team (captain)",
)
async def register_team(
    tournament_id: uuid.UUID,
    body: TeamRef,
    session: Annotated[AsyncSession, Depends(_CAPTAIN)],
) -> RegistrationResponse:
    try:
        reg = await service.register_team(
            session, tournament_id=tournament_id, team_id=body.team_id
        )
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
    except service.TournamentFullError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (service.RegistrationClosedError, service.InvalidStateError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _reg_response(reg)


@router.post(
    "/{tournament_id}/check-in",
    response_model=RegistrationResponse,
    summary="Check a team in (captain)",
)
async def check_in(
    tournament_id: uuid.UUID,
    body: TeamRef,
    session: Annotated[AsyncSession, Depends(_CAPTAIN)],
) -> RegistrationResponse:
    try:
        reg = await service.check_in(session, tournament_id=tournament_id, team_id=body.team_id)
    except service.InvalidStateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _reg_response(reg)


@router.post(
    "/{tournament_id}/start",
    response_model=StartResponse,
    summary="Generate the bracket and start (guild_admin)",
)
async def start_tournament(
    tournament_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(_ADMIN)],
) -> StartResponse:
    try:
        result = await service.start_tournament(session, tournament_id=tournament_id)
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
    except (service.NotEnoughTeamsError, service.InvalidStateError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return StartResponse(
        tournament_id=result.tournament_id,
        matches_created=result.matches_created,
        rounds=result.rounds,
        byes=result.byes,
    )
