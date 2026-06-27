"""Team CRUD + membership management (PLAN.md §2 data model, §3.1 teams/).

Thin HTTP + authorization shell over :mod:`app.teams.service`. Every route runs on
the tenant session (RLS-scoped to the acting guild). Creating a team and managing
its membership requires ``captain`` (or ``guild_admin``); listing/getting only
requires a tenant session (any role in the guild). The acting guild comes from the
``X-Guild-Id`` header or the JWT claim (these routes key on ``team_id``, not a path
guild).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from lol_schemas.enums import TeamMemberRole, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    Principal,
    get_current_principal,
    get_tenant_session,
    require_tenant_role,
)
from app.models.tables import Team, TeamMember
from app.teams import service

router = APIRouter(prefix="/teams", tags=["teams"])

_CAPTAIN = require_tenant_role(UserGuildRoleName.CAPTAIN, UserGuildRoleName.GUILD_ADMIN)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    captain_user_id: uuid.UUID


class TeamResponse(BaseModel):
    id: str
    name: str
    captain_user_id: str
    guild_id: str


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: TeamMemberRole = TeamMemberRole.PLAYER


class MemberRef(BaseModel):
    user_id: uuid.UUID


class TeamMemberResponse(BaseModel):
    team_id: str
    user_id: str
    role: str


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _to_response(t: Team) -> TeamResponse:
    return TeamResponse(
        id=str(t.id),
        name=t.name,
        captain_user_id=str(t.captain_user_id),
        guild_id=str(t.guild_id),
    )


def _member_response(m: TeamMember) -> TeamMemberResponse:
    return TeamMemberResponse(
        team_id=str(m.team_id),
        user_id=str(m.user_id),
        role=_enum_value(m.role),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("", response_model=TeamResponse, summary="Create a team (captain)")
async def create_team(
    body: CreateTeamRequest,
    session: Annotated[AsyncSession, Depends(_CAPTAIN)],
) -> TeamResponse:
    team = await service.create_team(session, name=body.name, captain_user_id=body.captain_user_id)
    return _to_response(team)


@router.get("", response_model=list[TeamResponse], summary="List teams in this guild")
async def list_teams(
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TeamResponse]:
    return [_to_response(t) for t in await service.list_teams(session)]


@router.get("/{team_id}", response_model=TeamResponse, summary="Get a team")
async def get_team(
    team_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TeamResponse:
    try:
        return _to_response(await service.get_team(session, team_id=team_id))
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc


@router.get(
    "/{team_id}/members",
    response_model=list[TeamMemberResponse],
    summary="List a team's members",
)
async def list_members(
    team_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TeamMemberResponse]:
    try:
        members = await service.list_members(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    return [_member_response(m) for m in members]


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    summary="Add a player/sub to a team (captain)",
)
async def add_member(
    team_id: uuid.UUID,
    body: AddMemberRequest,
    session: Annotated[AsyncSession, Depends(_CAPTAIN)],
) -> TeamMemberResponse:
    try:
        member = await service.add_member(
            session, team_id=team_id, user_id=body.user_id, role=body.role
        )
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    except service.DuplicateMemberError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="user already on team") from exc
    except service.InvalidRoleError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _member_response(member)


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a team (captain)",
)
async def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(_CAPTAIN)],
) -> None:
    try:
        await service.remove_member(session, team_id=team_id, user_id=user_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    except service.MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found") from exc
    except service.CaptainProtectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/{team_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave a team (the acting member removes THEMSELVES only)",
)
async def leave_team(
    team_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    # Strictly self-service: the member id is the authenticated caller, never a
    # request-body value — otherwise any guild member could remove any other.
    # Captain-driven removal of others stays behind the _CAPTAIN delete route.
    try:
        await service.leave_team(session, team_id=team_id, user_id=uuid.UUID(principal.user_id))
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    except service.MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found") from exc
    except service.CaptainProtectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
