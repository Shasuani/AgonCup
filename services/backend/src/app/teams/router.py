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
# Creating a team is open to ANY guild member: the creator becomes the team's
# captain (a TeamMember role), so gating it on a pre-existing CAPTAIN guild-role
# would be circular. `player` is the baseline every paired member holds.
_MEMBER = require_tenant_role(
    UserGuildRoleName.PLAYER, UserGuildRoleName.CAPTAIN, UserGuildRoleName.GUILD_ADMIN
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Optional: the desktop client omits this and the acting principal becomes the
    # captain. The Discord/admin path may still name a captain explicitly.
    captain_user_id: uuid.UUID | None = None


class TeamResponse(BaseModel):
    id: str
    name: str
    captain_user_id: str
    guild_id: str


class CreatedTeamResponse(TeamResponse):
    """Create result — carries the ``join_code`` the captain shares with players."""

    join_code: str


class JoinCodeResponse(BaseModel):
    team_id: str
    join_code: str


class JoinByCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: TeamMemberRole = TeamMemberRole.PLAYER


class MemberRef(BaseModel):
    user_id: uuid.UUID


class TeamMemberResponse(BaseModel):
    team_id: str
    user_id: str
    role: str
    # Present on roster listings (JOINed to users); None on the bare approve result.
    display_name: str | None = None


class JoinRequestResponse(BaseModel):
    """A pending join request surfaced to the captain (with the joiner's name)."""

    team_id: str
    team_name: str
    user_id: str
    display_name: str


class MyTeamResponse(BaseModel):
    """A team the caller belongs to, with their own role + membership status."""

    team_id: str
    name: str
    role: str
    status: str


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _ensure_team_captain(team: Team, principal: Principal) -> None:
    """Authorize a per-team captain action. A team's *own* captain (a TeamMember
    role, distinct from the guild-level ``captain`` role) may manage its join
    requests, as may a guild admin / platform operator. Raises 403 otherwise."""
    is_captain = str(team.captain_user_id) == principal.user_id
    is_admin = (
        principal.is_platform_operator or UserGuildRoleName.GUILD_ADMIN.value in principal.roles
    )
    if not (is_captain or is_admin):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the team captain can manage join requests"
        )


def _join_request_response(r: service.JoinRequest) -> JoinRequestResponse:
    return JoinRequestResponse(
        team_id=str(r.team_id),
        team_name=r.team_name,
        user_id=str(r.user_id),
        display_name=r.display_name,
    )


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
@router.post("", response_model=CreatedTeamResponse, summary="Create a team (any member)")
async def create_team(
    body: CreateTeamRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(_MEMBER)],
) -> CreatedTeamResponse:
    # The desktop client omits captain_user_id → the acting principal captains the
    # team they just created (the client-first "team leader" flow, PLAN.md §3.1).
    captain_id = body.captain_user_id or uuid.UUID(principal.user_id)
    team = await service.create_team(session, name=body.name, captain_user_id=captain_id)
    base = _to_response(team)
    return CreatedTeamResponse(**base.model_dump(), join_code=team.join_code or "")


@router.get("", response_model=list[TeamResponse], summary="List teams in this guild")
async def list_teams(
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[TeamResponse]:
    return [_to_response(t) for t in await service.list_teams(session)]


@router.post(
    "/join-by-code",
    response_model=TeamResponse,
    summary="Request to join a team by its code (pending captain approval)",
)
async def join_by_code(
    body: JoinByCodeRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TeamResponse:
    # Redeeming a code creates a PENDING request, not immediate membership — the
    # captain approves/rejects it (see the /join-requests routes below).
    try:
        team, _ = await service.join_by_code(
            session, code=body.code, user_id=uuid.UUID(principal.user_id)
        )
    except service.InvalidJoinCodeError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invalid join code") from exc
    except service.TeamFullError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"team is full ({exc} players max)"
        ) from exc
    return _to_response(team)


# NOTE: this literal route MUST precede ``/{team_id}`` so "join-requests" is not
# captured as a (non-UUID) team id.
@router.get(
    "/join-requests/mine",
    response_model=list[JoinRequestResponse],
    summary="Pending join requests across teams the caller captains (notify feed)",
)
async def my_join_requests(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[JoinRequestResponse]:
    rows = await service.list_my_join_requests(
        session, captain_user_id=uuid.UUID(principal.user_id)
    )
    return [_join_request_response(r) for r in rows]


# NOTE: literal route — must precede ``/{team_id}`` (see the note above).
@router.get(
    "/mine",
    response_model=list[MyTeamResponse],
    summary="List the teams the caller belongs to (with their role + status)",
)
async def my_teams(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[MyTeamResponse]:
    rows = await service.list_my_teams(session, user_id=uuid.UUID(principal.user_id))
    return [
        MyTeamResponse(
            team_id=str(r.team_id),
            name=r.name,
            role=_enum_value(r.role),
            status=_enum_value(r.status),
        )
        for r in rows
    ]


@router.get("/{team_id}", response_model=TeamResponse, summary="Get a team")
async def get_team(
    team_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TeamResponse:
    try:
        return _to_response(await service.get_team(session, team_id=team_id))
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disband a team (captain of that team)",
)
async def delete_team(
    team_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
    await service.delete_team(session, team_id=team_id)


@router.get(
    "/{team_id}/join-code",
    response_model=JoinCodeResponse,
    summary="Get a team's join code (captain of that team, or guild_admin)",
)
async def get_join_code(
    team_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> JoinCodeResponse:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    is_captain = str(team.captain_user_id) == principal.user_id
    is_admin = (
        principal.is_platform_operator or UserGuildRoleName.GUILD_ADMIN.value in principal.roles
    )
    if not (is_captain or is_admin):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the team captain can read the join code"
        )
    return JoinCodeResponse(team_id=str(team.id), join_code=team.join_code or "")


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
        roster = await service.list_roster(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    return [
        TeamMemberResponse(
            team_id=str(team_id),
            user_id=str(r.user_id),
            role=_enum_value(r.role),
            display_name=r.display_name,
        )
        for r in roster
    ]


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
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
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


@router.post(
    "/{team_id}/transfer-captain",
    response_model=TeamResponse,
    summary="Transfer team captaincy to another active member (captain)",
)
async def transfer_captain(
    team_id: uuid.UUID,
    body: MemberRef,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TeamResponse:
    """Hand captaincy to another ACTIVE member (edge-cases §1.4).

    Lets a captain who wants to leave pass the team on instead of disbanding: the
    old captain becomes a ``player`` and can then leave. Captain-only.
    """
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
    try:
        team = await service.transfer_captain(
            session, team_id=team_id, new_captain_user_id=body.user_id
        )
    except service.NewCaptainNotEligibleError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="captaincy can only pass to an active member of the team",
        ) from exc
    return _to_response(team)


# ---------------------------------------------------------------------------
# Team-leader approval flow: pending join requests (PLAN.md §3.1)
# ---------------------------------------------------------------------------
@router.get(
    "/{team_id}/join-requests",
    response_model=list[JoinRequestResponse],
    summary="List a team's pending join requests (captain of that team)",
)
async def team_join_requests(
    team_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[JoinRequestResponse]:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
    rows = await service.list_join_requests(session, team_id=team_id)
    return [_join_request_response(r) for r in rows]


@router.post(
    "/{team_id}/join-requests/{user_id}/approve",
    response_model=TeamMemberResponse,
    summary="Approve a pending join request (captain of that team)",
)
async def approve_join_request(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TeamMemberResponse:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
    try:
        member = await service.approve_join_request(session, team_id=team_id, user_id=user_id)
    except service.MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no pending request") from exc
    except service.TeamFullError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"team is full ({exc} players max)"
        ) from exc
    return _member_response(member)


@router.post(
    "/{team_id}/join-requests/{user_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reject a pending join request (captain of that team)",
)
async def reject_join_request(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    try:
        team = await service.get_team(session, team_id=team_id)
    except service.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    _ensure_team_captain(team, principal)
    try:
        await service.reject_join_request(session, team_id=team_id, user_id=user_id)
    except service.MemberNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no pending request") from exc
