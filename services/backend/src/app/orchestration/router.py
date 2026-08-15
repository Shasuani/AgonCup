"""Host designation, lobby state machine, WS command issuance, failover (PLAN.md §3.6.1).

Thin HTTP + authorization shell over :mod:`app.orchestration.service`. Every route
runs on the tenant session (RLS-scoped to the acting guild). The privileged
lifecycle action (initialize a match lobby) requires ``guild_admin`` in the acting
guild; the authenticated client-ingestion endpoints (heartbeat, lobby-state ack,
trigger-failover) run on the tenant session — the host-epoch lease, not the HTTP
role, is the security boundary for these (a stale/deposed host is rejected by the
pure :func:`lol_core.reject_stale_ack` fence inside the service, never by RBAC).

The acting guild comes from the ``X-Guild-Id`` header or the JWT claim (these
routes key on ``match_id``, not a path guild).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from lol_core import FailoverAction, LobbyEvent
from lol_schemas.enums import LobbyPhase, UserGuildRoleName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Principal, get_current_principal, get_tenant_session, require_tenant_role
from app.orchestration import service


def _command_publisher() -> service.CommandPublisher:
    """Build the production command publisher (Redis -> the WS hub's channel).

    Deferred imports keep orchestration's module load light and avoid any
    import-order coupling with the realtime package. This closes the loop: minted
    commands are published on the per-guild/per-match channel the WS hub relays to
    the subscribed desktop clients (PLAN.md §3.6.1 / §5).
    """
    from app.realtime.hub import RedisCommandPublisher  # noqa: PLC0415
    from app.realtime.redis_client import get_redis  # noqa: PLC0415

    return RedisCommandPublisher(get_redis())


router = APIRouter(prefix="/orchestration", tags=["orchestration"])

# Initializing a match lobby (host designation + first commands) is a privileged
# action in the acting guild (PLAN.md §3.6.1).
_ADMIN = require_tenant_role(UserGuildRoleName.GUILD_ADMIN)


@router.get("/_ping", summary="orchestration module liveness placeholder")
async def ping() -> dict[str, str]:
    """Placeholder endpoint proving the router is mounted."""
    return {"module": "orchestration", "status": "ok"}


class MyMatchResponse(BaseModel):
    """One live match the caller is rostered in (see :class:`service.MyMatch`)."""

    match_id: str
    guild_id: str
    tournament_name: str
    round_label: str
    lobby_state: str | None = None
    lobby_name: str | None = None
    lobby_password: str | None = None
    host_name: str | None = None
    is_host: bool = False


@router.get(
    "/matches/mine",
    response_model=list[MyMatchResponse],
    summary="List the caller's live matches and their lobby state",
)
async def list_my_matches(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[MyMatchResponse]:
    """What a desktop client needs to join its own matches.

    The missing half of the realtime plane. The backend published every
    orchestration command onto a per-match channel, but a client only ever
    subscribed to a match id supplied through the ``DESKTOP_MATCH_ID`` environment
    variable — dev wiring, never replaced — so in a real tournament nobody was
    listening and the commands went nowhere.

    Also carries the lobby's live state, name and password, which is what lets the
    client *show* the player whether the custom game exists yet and, when nobody
    could host it, exactly what to create by hand.
    """
    rows = await service.list_my_matches(session, user_id=uuid.UUID(principal.user_id))
    return [
        MyMatchResponse(
            match_id=m.match_id,
            guild_id=m.guild_id,
            tournament_name=m.tournament_name,
            round_label=m.round_label,
            lobby_state=m.lobby_state,
            lobby_name=m.lobby_name,
            lobby_password=m.lobby_password,
            host_name=m.host_name,
            is_host=m.is_host,
        )
        for m in rows
    ]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class InitializeLobbyRequest(BaseModel):
    lobby_name: str = Field(min_length=1, max_length=120)
    lobby_password: str = Field(min_length=1, max_length=120)


class LobbyResponse(BaseModel):
    lobby_id: str
    match_id: str
    host_puuid: str
    host_epoch: int
    succession: list[str]
    state: str


class HeartbeatRequest(BaseModel):
    host_epoch: int = Field(ge=0)
    phase: LobbyPhase


class LobbyStateAckRequest(BaseModel):
    host_epoch: int = Field(ge=0)
    event: LobbyEvent
    lcu_lobby_id: str | None = None


class IngestResponse(BaseModel):
    accepted: bool
    host_epoch: int
    state: str
    detail: str = ""


class TriggerFailoverRequest(BaseModel):
    host_connected: bool = False
    missed_heartbeats: int = Field(ge=0)
    gameflow_advanced: bool = False


class FailoverResponse(BaseModel):
    action: str
    host_epoch: int
    host_puuid: str | None
    state: str
    reason: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/matches/{match_id}/lobby",
    response_model=LobbyResponse,
    summary="Create + initialize a match lobby with a designated host (guild_admin)",
)
async def initialize_lobby(
    match_id: uuid.UUID,
    body: InitializeLobbyRequest,
    session: Annotated[AsyncSession, Depends(_ADMIN)],
) -> LobbyResponse:
    """Create the ``MatchLobby``, designate the host, and issue create+invite.

    Idempotent: a second call returns the existing lobby. ``404`` if the match is
    not in the acting guild; ``409`` if nobody on the checked-in roster opted in to
    host (PLAN.md §3.6.1).
    """
    try:
        result = await service.initialize_lobby(
            session,
            match_id=match_id,
            lobby_name=body.lobby_name,
            lobby_password=body.lobby_password,
            publisher=_command_publisher(),
        )
    except service.MatchNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="match not found") from exc
    except service.NoEligibleHostError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="no checked-in roster member opted in to host"
        ) from exc
    return LobbyResponse(
        lobby_id=result.lobby_id,
        match_id=result.match_id,
        host_puuid=result.host_puuid,
        host_epoch=result.host_epoch,
        succession=list(result.succession),
        state=result.state.value,
    )


@router.post(
    "/matches/{match_id}/heartbeat",
    response_model=IngestResponse,
    summary="Ingest a pre-game host heartbeat (epoch-fenced)",
)
async def ingest_heartbeat(
    match_id: uuid.UUID,
    body: HeartbeatRequest,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> IngestResponse:
    """Record host liveness, rejecting a stale ``host_epoch`` (PLAN.md §3.6.1).

    ``404`` if no lobby exists for the match; ``409`` if the heartbeat carries a
    stale (non-current) epoch (a deposed/flapping host — split-brain refusal).
    """
    try:
        result = await service.ingest_heartbeat(
            session, match_id=match_id, host_epoch=body.host_epoch, phase=body.phase
        )
    except service.LobbyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lobby not found") from exc
    except service.StaleEpochError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _ingest_response(result)


@router.post(
    "/matches/{match_id}/lobby-state",
    response_model=IngestResponse,
    summary="Ingest + ack a host lobby-state transition (epoch-fenced)",
)
async def ingest_lobby_state(
    match_id: uuid.UUID,
    body: LobbyStateAckRequest,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> IngestResponse:
    """Advance the lobby phase from a host ack (PLAN.md §3.6.1).

    The transition is persisted through the pure state machine. ``404`` if no
    lobby exists; ``409`` if the ack carries a stale epoch or drives an illegal
    transition.
    """
    try:
        result = await service.ingest_lobby_state(
            session,
            match_id=match_id,
            host_epoch=body.host_epoch,
            event=body.event,
            lcu_lobby_id=body.lcu_lobby_id,
        )
    except service.LobbyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lobby not found") from exc
    except (service.StaleEpochError, service.InvalidStateError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _ingest_response(result)


@router.post(
    "/matches/{match_id}/failover",
    response_model=FailoverResponse,
    summary="Trigger a host-drop failover decision (promote / recreate / abort)",
)
async def trigger_failover(
    match_id: uuid.UUID,
    body: TriggerFailoverRequest,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> FailoverResponse:
    """Resolve + apply a host-drop failover (PLAN.md §3.6.1).

    The decision is delegated to the pure state machine; this endpoint persists it
    and dispatches the matching command. ``404`` if no lobby exists.
    """
    try:
        result = await service.trigger_failover(
            session,
            match_id=match_id,
            host_connected=body.host_connected,
            missed_heartbeats=body.missed_heartbeats,
            gameflow_advanced=body.gameflow_advanced,
            publisher=_command_publisher(),
        )
    except service.LobbyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="lobby not found") from exc
    return FailoverResponse(
        action=result.action.value
        if isinstance(result.action, FailoverAction)
        else str(result.action),
        host_epoch=result.host_epoch,
        host_puuid=result.host_puuid,
        state=result.state.value,
        reason=result.reason,
    )


def _ingest_response(result: service.IngestResult) -> IngestResponse:
    return IngestResponse(
        accepted=result.accepted,
        host_epoch=result.host_epoch,
        state=result.state.value,
        detail=result.detail,
    )
