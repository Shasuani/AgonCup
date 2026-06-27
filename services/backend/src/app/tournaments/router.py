"""Tournament CRUD, registration/check-in, and bracket start (PLAN.md §3.1).

Thin HTTP + authorization shell over :mod:`app.tournaments.service`. Every route
runs on the tenant session (RLS-scoped to the acting guild). Creating/starting a
tournament requires ``guild_admin``; registering/checking-in a team requires
``captain`` (or ``guild_admin``). The acting guild comes from the ``X-Guild-Id``
header or the JWT claim (these routes key on ``tournament_id``, not a path guild).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from lol_schemas.enums import TournamentFormat, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_tenant_session, require_tenant_role
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
    max_teams: int = Field(default=16, ge=2, le=16)
    ruleset: dict[str, Any] = Field(default_factory=dict)


class TournamentResponse(BaseModel):
    id: str
    name: str
    status: str
    format: str
    max_teams: int
    is_shared_to_central: bool


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


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _to_response(t: Tournament) -> TournamentResponse:
    return TournamentResponse(
        id=str(t.id),
        name=t.name,
        status=_enum_value(t.status),
        format=_enum_value(t.format),
        max_teams=t.max_teams,
        is_shared_to_central=t.is_shared_to_central,
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
@router.post("", response_model=TournamentResponse, summary="Create a tournament (guild_admin)")
async def create_tournament(
    body: CreateTournamentRequest,
    session: Annotated[AsyncSession, Depends(_ADMIN)],
) -> TournamentResponse:
    try:
        t = await service.create_tournament(
            session,
            name=body.name,
            format=body.format,
            max_teams=body.max_teams,
            ruleset=body.ruleset,
        )
    except service.InvalidStateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_response(t)


@router.get("", response_model=list[TournamentResponse], summary="List tournaments in this guild")
async def list_tournaments(
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TournamentResponse]:
    return [_to_response(t) for t in await service.list_tournaments(session)]


@router.get("/{tournament_id}", response_model=TournamentResponse, summary="Get a tournament")
async def get_tournament(
    tournament_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TournamentResponse:
    try:
        return _to_response(await service.get_tournament(session, tournament_id=tournament_id))
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc


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
