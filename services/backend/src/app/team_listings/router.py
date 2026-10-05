"""Team listings HTTP shell — the desktop "Find a team" screen (PLAN.md §3.1).

Thin authorization + error-mapping layer over :mod:`app.team_listings.service`.
Every route runs on the caller's tenant session; the service is what reaches a
listed team's own server (see its module docstring for exactly when and why).

* ``GET    /team-listings``                — every open listing (``?region=euw1``).
* ``POST   /team-listings``                — the captain posts their team. The
  client sends the team's server as ``X-Guild-Id``, like every per-team call.
* ``DELETE /team-listings/{id}``           — the team's captain or a platform operator.
* ``POST   /team-listings/{id}/request``   — ask to join (pending captain approval).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Principal, get_current_principal, get_tenant_session
from app.team_listings import service
from app.teams import service as teams

router = APIRouter(prefix="/team-listings", tags=["team-listings"])


class CreateListingRequest(BaseModel):
    team_id: uuid.UUID
    region: str = Field(min_length=2, max_length=8)
    message: str | None = Field(default=None, max_length=400)


class ListingMemberResponse(BaseModel):
    display_name: str
    role: str
    rank_text: str


class ListingResponse(BaseModel):
    id: str
    team_id: str
    #: The team's server. A member of the team sends it as ``X-Guild-Id``.
    guild_id: str
    team_name: str
    server_name: str
    region: str
    message: str | None
    created_at: datetime
    member_count: int
    max_members: int
    members: list[ListingMemberResponse]
    #: The caller's tie to the team: "captain" / "member" / "pending", or None.
    viewer_status: str | None
    can_remove: bool


class CreatedListingResponse(BaseModel):
    id: str
    team_id: str
    guild_id: str
    region: str
    message: str | None


class JoinRequestedResponse(BaseModel):
    team_id: str
    team_name: str
    guild_id: str
    #: "pending" for a fresh request; "active" if the caller was already on it.
    status: str


def _value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _listing_response(v: service.ListingView) -> ListingResponse:
    return ListingResponse(
        id=str(v.listing_id),
        team_id=str(v.team_id),
        guild_id=str(v.guild_id),
        team_name=v.team_name,
        server_name=v.server_name,
        region=v.region,
        message=v.message,
        created_at=v.created_at,
        member_count=v.member_count,
        max_members=teams.MAX_TEAM_SIZE,
        members=[
            ListingMemberResponse(
                display_name=m.display_name, role=_value(m.role), rank_text=m.rank_text
            )
            for m in v.members
        ],
        viewer_status=v.viewer_status,
        can_remove=v.can_remove,
    )


def _region_required(exc: service.RegionAccountRequiredError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail=f"You need a verified {exc.region_label} account — link one in Accounts first.",
    )


@router.get("", response_model=list[ListingResponse], summary="Open team listings, every server")
async def list_listings(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
    region: Annotated[str | None, Query(max_length=8)] = None,
) -> list[ListingResponse]:
    try:
        views = await service.list_listings(
            session,
            viewer_id=uuid.UUID(principal.user_id),
            is_platform_operator=principal.is_platform_operator,
            region=region,
        )
    except service.InvalidRegionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return [_listing_response(v) for v in views]


@router.post(
    "",
    response_model=CreatedListingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post your team as looking for players (team captain)",
)
async def create_listing(
    body: CreateListingRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> CreatedListingResponse:
    try:
        listing = await service.create_listing(
            session,
            team_id=body.team_id,
            user_id=uuid.UUID(principal.user_id),
            region=body.region,
            message=body.message,
        )
    except teams.TeamNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="team not found") from exc
    except service.NotTeamCaptainError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the team captain can post the team"
        ) from exc
    except service.InvalidRegionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except service.RegionAccountRequiredError as exc:
        raise _region_required(exc) from exc
    except teams.TeamFullError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="your team is already full") from exc
    except service.ListingExistsError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="your team already has a post — delete it first to post a new one",
        ) from exc
    except service.ListingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return CreatedListingResponse(
        id=str(listing.id),
        team_id=str(listing.team_id),
        guild_id=str(listing.guild_id),
        region=listing.region,
        message=listing.message,
    )


@router.delete(
    "/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a listing down (team captain or platform operator)",
)
async def delete_listing(
    listing_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    try:
        await service.delete_listing(
            session,
            listing_id=listing_id,
            user_id=uuid.UUID(principal.user_id),
            is_platform_operator=principal.is_platform_operator,
        )
    except service.ListingNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="listing not found") from exc
    except service.NotTeamCaptainError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the team captain can delete this post"
        ) from exc


@router.post(
    "/{listing_id}/request",
    response_model=JoinRequestedResponse,
    summary="Ask to join a listed team, from any server (pending captain approval)",
)
async def request_to_join(
    listing_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> JoinRequestedResponse:
    try:
        result = await service.request_to_join(
            session, listing_id=listing_id, user_id=uuid.UUID(principal.user_id)
        )
    except (service.ListingNotFoundError, teams.TeamNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="listing not found") from exc
    except service.RegionAccountRequiredError as exc:
        raise _region_required(exc) from exc
    except teams.TeamFullError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="this team is already full") from exc
    return JoinRequestedResponse(
        team_id=str(result.team.id),
        team_name=result.team.name,
        guild_id=str(result.team.guild_id),
        status=_value(result.member.status),
    )
