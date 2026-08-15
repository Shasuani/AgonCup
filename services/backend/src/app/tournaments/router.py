"""Tournament CRUD, registration/check-in, and bracket start (PLAN.md §3.1).

Thin HTTP + authorization shell over :mod:`app.tournaments.service`. Every route
runs on the tenant session (RLS-scoped to the acting guild). Creating/starting a
tournament requires ``guild_admin``; registering/checking-in a team requires
``captain`` (or ``guild_admin``). The acting guild comes from the ``X-Guild-Id``
header or the JWT claim (these routes key on ``tournament_id``, not a path guild).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
from app.logging import get_logger
from app.models.tables import Registration, Tournament
from app.tournaments import readiness, service

logger = get_logger(__name__)
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


class CheckInRequest(BaseModel):
    """Lock-in body. ``team_id`` is optional — a solo (1v1) entrant has no team
    to name, and a captain of exactly one team does not need to repeat it."""

    team_id: uuid.UUID | None = None
    #: "May your client create and run the lobby for your matches?" Hosting is the
    #: one path that issues LCU *writes*, so PLAN.md §3.6.1 requires an explicit
    #: opt-in — omitting it leaves any standing answer alone and never opts anyone
    #: in by default. Matches where nobody opted in fall back to the manual
    #: name+password DM rather than going quiet.
    host_opt_in: bool | None = None


class EntrantPlayerResponse(BaseModel):
    """A roster member of an entry, with their own lock-in state."""

    user_id: str
    name: str
    locked_in: bool
    is_you: bool


class EntrantResponse(BaseModel):
    """One tournament entry: a team in 5v5, a player (their solo team) in 1v1."""

    team_id: str
    name: str
    locked_in: bool
    is_solo: bool
    is_you: bool
    can_lock_in: bool
    players: list[EntrantPlayerResponse] = []


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
    id: str
    round: int
    slot: int
    team_a_id: str | None
    team_a_name: str | None
    team_b_id: str | None
    team_b_name: str | None
    winner_team_id: str | None
    status: str
    ready_deadline_at: str | None = None
    team_a_ready: bool = False
    team_b_ready: bool = False


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


@router.delete(
    "/{tournament_id}/mine",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a finished tournament off the caller's own list",
)
async def hide_my_tournament(
    tournament_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> None:
    """Per-user dismissal — NOT a delete.

    ``/mine`` only ever grows, so after a season the list a player actually cares
    about is buried. This drops one tournament from *the caller's* view; every
    other entrant's list and the guild's record are untouched, and no admin role
    is needed (you are only editing your own screen).

    Restricted to completed/cancelled tournaments: hiding a live one would remove
    the caller's own way back to lock-in and the bracket.
    """
    try:
        await service.hide_tournament(
            session,
            tournament_id=tournament_id,
            user_id=uuid.UUID(principal.user_id),
        )
    except service.TournamentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tournament not found") from exc
    except service.TournamentNotFinishedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="only a completed or cancelled tournament can be removed from your list",
        ) from exc


@router.get(
    "/by-code/{code}",
    response_model=TournamentResponse,
    summary="Look up a tournament by its join code, without enrolling",
)
async def get_by_code(
    code: str,
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> TournamentResponse:
    """Read-only preview of what a join code points at.

    The client needs the ``game_mode`` **before** it enrols: a 5v5 code has to ask
    a multi-team captain which team is entering, while a 1v1 code must never ask
    that. Enrolling first and recovering from an error would join the wrong team.
    RLS scopes the lookup, so a foreign guild's code reads as invalid.
    """
    try:
        return _to_response(await service.get_tournament_by_code(session, code=code))
    except service.InvalidJoinCodeError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invalid join code") from exc


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
                id=str(m.id),
                round=m.round,
                slot=m.slot,
                team_a_id=str(m.team_a_id) if m.team_a_id else None,
                team_a_name=m.team_a_name,
                team_b_id=str(m.team_b_id) if m.team_b_id else None,
                team_b_name=m.team_b_name,
                winner_team_id=str(m.winner_team_id) if m.winner_team_id else None,
                status=m.status,
                ready_deadline_at=(
                    m.ready_deadline_at.isoformat() if m.ready_deadline_at else None
                ),
                team_a_ready=m.team_a_ready,
                team_b_ready=m.team_b_ready,
            )
            for m in matches
        ],
    )


class ReadyResponse(BaseModel):
    match_id: str
    side: str
    both_ready: bool


@router.post(
    "/matches/{match_id}/ready",
    response_model=ReadyResponse,
    summary="Mark the caller's team ready for a match (team captain)",
)
async def ready_up(
    match_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> ReadyResponse:
    """Team captain presses **ready** for one of the match's two teams.

    The caller must captain ``team_a`` or ``team_b`` (edge-cases §8.5). Idempotent;
    once both sides ready the match's ready window is disarmed.
    """
    try:
        team_id = await readiness.resolve_ready_team(
            session, match_id=match_id, user_id=uuid.UUID(principal.user_id)
        )
        outcome = await readiness.mark_team_ready(
            session, match_id=match_id, team_id=team_id, now=datetime.now(UTC)
        )
    except readiness.MatchNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="match not found") from exc
    except readiness.NotTeamCaptainError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="you do not captain a team in this match"
        ) from exc
    except readiness.MatchNotReadyableError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Both sides ready → autonomously spin up the lobby (edge-cases §8.1/§8.5).
    # Best-effort: a realtime/roster hiccup must not fail the ready acknowledgement
    # (an admin can initialize the lobby manually as a fallback).
    if outcome.both_ready:
        from app.orchestration import service as orchestration_service

        try:
            await orchestration_service.auto_initialize_lobby(
                session,
                match_id=match_id,
                publisher=orchestration_service.make_command_publisher(),
            )
        except Exception:  # noqa: BLE001 — best-effort side effect, never blocks ready.
            logger.exception("ready_up.auto_lobby_failed", match_id=str(match_id))

    return ReadyResponse(match_id=str(match_id), side=outcome.side, both_ready=outcome.both_ready)


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
    summary="Lock in for a tournament (solo player, or a team's captain)",
)
async def check_in(
    tournament_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
    body: CheckInRequest | None = None,
) -> RegistrationResponse:
    """Lock in — the last step before an admin can start the bracket.

    **Every player locks themselves in.** The captain redeems the join code and the
    whole roster is entered, but their word is not evidence that four other people
    are at their keyboards, so the team's registration only reaches CHECKED_IN once
    each active member has pressed this. A 1v1 entrant is a roster of one, so the
    same call covers them.

    Omitting ``team_id`` — what every client does — means "lock me in". Passing one
    is the **admin override**: it forces that whole team in, for a bracket held up
    by a teammate who never showed. Not gated on the ``captain`` guild role (a 1v1
    entrant is an ordinary member), so authorization is enforced here.
    """
    team_id = body.team_id if body is not None else None
    try:
        if team_id is None:
            reg = await service.check_in_self(
                session,
                tournament_id=tournament_id,
                user_id=uuid.UUID(principal.user_id),
                host_opt_in=body.host_opt_in if body is not None else None,
            )
        else:
            if UserGuildRoleName.GUILD_ADMIN not in principal.roles:
                # Forcing a whole team in is an admin capability; a captain naming
                # their own team gets the same override, nobody else does.
                await service.assert_team_captain(
                    session, team_id=team_id, user_id=uuid.UUID(principal.user_id)
                )
            reg = await service.check_in(session, tournament_id=tournament_id, team_id=team_id)
    except service.NotTeamCaptainError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the team captain can lock the team in"
        ) from exc
    except service.NoCaptainTeamError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="you have not joined this tournament"
        ) from exc
    except service.AmbiguousCaptainTeamError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="you captain multiple teams — specify team_id"
        ) from exc
    except service.InvalidStateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _reg_response(reg)


@router.get(
    "/{tournament_id}/entrants",
    response_model=list[EntrantResponse],
    summary="Who has entered a tournament, and who has locked in",
)
async def list_entrants(
    tournament_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> list[EntrantResponse]:
    entrants = await service.list_entrants(
        session, tournament_id=tournament_id, user_id=uuid.UUID(principal.user_id)
    )
    return [
        EntrantResponse(
            team_id=str(e.team_id),
            name=e.name,
            locked_in=e.locked_in,
            is_solo=e.is_solo,
            is_you=e.is_you,
            can_lock_in=e.can_lock_in,
            players=[
                EntrantPlayerResponse(
                    user_id=str(p.user_id),
                    name=p.name,
                    locked_in=p.locked_in,
                    is_you=p.is_you,
                )
                for p in e.players
            ],
        )
        for e in entrants
    ]


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

    # Drawing the bracket is only half of starting: the round-one matches have to
    # be handed to the players. Without this the tournament went RUNNING and every
    # entrant sat there, because opening a lobby was reachable only through a
    # per-match ready-up no client exposes. Best-effort — a notification or
    # realtime hiccup must not undo a bracket that was generated successfully.
    from app.orchestration import service as orchestration_service

    try:
        await orchestration_service.open_playable_matches(
            session,
            tournament_id=tournament_id,
            publisher=orchestration_service.make_command_publisher(),
        )
    except Exception:  # noqa: BLE001 — best-effort side effect, never fails the start.
        logger.exception("start_tournament.open_matches_failed", tournament_id=str(tournament_id))

    return StartResponse(
        tournament_id=result.tournament_id,
        matches_created=result.matches_created,
        rounds=result.rounds,
        byes=result.byes,
    )
