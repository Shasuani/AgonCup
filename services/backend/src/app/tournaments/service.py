"""Tournament domain service: CRUD, registration/check-in, bracket start.

Guild scoping is enforced by RLS on the tenant session (the GUC is already set by
``get_tenant_session``); inserts read the acting guild back from the GUC via
:func:`_current_guild` so a row can never be written for the wrong tenant (the
``guild_isolation`` WITH CHECK would reject it anyway — this just supplies the
value). The same-guild invariant between tournament/team/registration/match is
additionally structural (composite FKs, PLAN.md §2).

Bracket generation reuses the unit-tested single-elimination engine in
``lol_core`` (PLAN.md §3.1 bracket engine). :func:`plan_bracket_matches` is a pure
function (no DB) so it is exhaustively testable on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lol_core.bracket import generate_single_elimination
from lol_schemas.enums import (
    MatchStatus,
    RegistrationStatus,
    TournamentFormat,
    TournamentStatus,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, Registration, Team, Tournament

MAX_TEAMS_HARD_CAP = 16


class TournamentError(Exception):
    """Base class for tournament service failures."""


class TournamentNotFoundError(TournamentError):
    """No tournament with that id in the acting guild."""


class RegistrationClosedError(TournamentError):
    """The tournament is not accepting registrations in its current state."""


class TournamentFullError(TournamentError):
    """The tournament already holds ``max_teams`` registrations."""


class NotEnoughTeamsError(TournamentError):
    """Fewer than two eligible teams to start a bracket."""


class InvalidStateError(TournamentError):
    """The tournament is not in a state that permits the requested transition."""


async def _current_guild(session: AsyncSession) -> uuid.UUID:
    """Read the acting guild from the transaction GUC (set by get_tenant_session)."""
    gid = (
        await session.execute(text("SELECT current_setting('app.current_guild', true)"))
    ).scalar_one_or_none()
    if not gid:
        # Fail closed: never write a guild-scoped row without a resolved tenant.
        raise InvalidStateError("no acting guild on the session")
    return uuid.UUID(gid)


# ---------------------------------------------------------------------------
# Pure bracket planning (no DB — exhaustively unit-tested)
# ---------------------------------------------------------------------------
def plan_bracket_matches(
    *,
    tournament_id: uuid.UUID,
    guild_id: uuid.UUID,
    seeded_team_ids: Sequence[uuid.UUID],
) -> list[Match]:
    """Map a single-elimination bracket onto ``Match`` rows (best seed first).

    Byes (auto-advanced first-round matches) are emitted as ``WALKOVER`` matches
    with ``winner_team_id`` pre-filled; real matches are ``PENDING`` with no
    winner. Later-round placeholders carry no teams yet (filled as results land).
    """
    bracket = generate_single_elimination(list(seeded_team_ids))
    matches: list[Match] = []
    for bm in bracket.all_matches():
        matches.append(
            Match(
                guild_id=guild_id,
                tournament_id=tournament_id,
                round=bm.round_no,
                bracket_slot=bm.slot,
                team_a=bm.team_a,
                team_b=bm.team_b,
                winner_team_id=bm.winner if bm.is_bye else None,
                status=MatchStatus.WALKOVER if bm.is_bye else MatchStatus.PENDING,
            )
        )
    return matches


# ---------------------------------------------------------------------------
# CRUD + lifecycle (async; tenant-scoped via RLS)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class StartResult:
    tournament_id: str
    matches_created: int
    rounds: int
    byes: int


async def create_tournament(
    session: AsyncSession,
    *,
    name: str,
    format: TournamentFormat = TournamentFormat.SINGLE_ELIMINATION,
    max_teams: int = MAX_TEAMS_HARD_CAP,
    ruleset: dict[str, Any] | None = None,
) -> Tournament:
    """Create a DRAFT tournament in the acting guild."""
    if not 2 <= max_teams <= MAX_TEAMS_HARD_CAP:
        raise InvalidStateError(f"max_teams must be in [2, {MAX_TEAMS_HARD_CAP}]")
    guild_id = await _current_guild(session)
    tournament = Tournament(
        guild_id=guild_id,
        name=name,
        format=format,
        max_teams=max_teams,
        status=TournamentStatus.DRAFT,
        ruleset_json=ruleset or {},
    )
    session.add(tournament)
    await session.flush()
    return tournament


async def get_tournament(session: AsyncSession, *, tournament_id: uuid.UUID) -> Tournament:
    """Load a tournament (RLS already scopes to the acting guild)."""
    t = (
        await session.execute(select(Tournament).where(Tournament.id == tournament_id))
    ).scalar_one_or_none()
    if t is None:
        raise TournamentNotFoundError(str(tournament_id))
    return t


async def list_tournaments(session: AsyncSession) -> list[Tournament]:
    return list((await session.execute(select(Tournament))).scalars().all())


async def register_team(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    """Register a team into a tournament (PENDING), enforcing the max_teams cap.

    The same-guild invariant is structural (composite FKs); RLS guarantees the
    tournament and team are both in the acting guild.
    """
    tournament = await get_tournament(session, tournament_id=tournament_id)
    if tournament.status not in (TournamentStatus.OPEN, TournamentStatus.DRAFT):
        raise RegistrationClosedError(tournament.status.value)

    # Team must exist in this guild (RLS-scoped).
    team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise InvalidStateError("team not found in this guild")

    count = (
        await session.execute(
            select(func.count())
            .select_from(Registration)
            .where(Registration.tournament_id == tournament_id)
        )
    ).scalar_one()
    if count >= tournament.max_teams:
        raise TournamentFullError(f"tournament is full ({tournament.max_teams})")

    guild_id = await _current_guild(session)
    reg = Registration(
        guild_id=guild_id,
        tournament_id=tournament_id,
        team_id=team_id,
        status=RegistrationStatus.PENDING,
    )
    session.add(reg)
    await session.flush()
    return reg


async def check_in(
    session: AsyncSession, *, tournament_id: uuid.UUID, team_id: uuid.UUID
) -> Registration:
    """Move an approved registration to CHECKED_IN (eligible for the bracket)."""
    reg = (
        await session.execute(
            select(Registration).where(
                Registration.tournament_id == tournament_id,
                Registration.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
    if reg is None:
        raise InvalidStateError("registration not found")
    reg.status = RegistrationStatus.CHECKED_IN
    await session.flush()
    return reg


async def _seeded_team_ids(session: AsyncSession, tournament_id: uuid.UUID) -> list[uuid.UUID]:
    """Checked-in teams in deterministic seed order (registration order = seed).

    v1 seeding is by registration order (stable, attacker-neutral). A skill-based
    seeding input can replace the ORDER BY later without touching the bracket map.
    """
    rows = (
        await session.execute(
            select(Registration.team_id)
            .where(
                Registration.tournament_id == tournament_id,
                Registration.status == RegistrationStatus.CHECKED_IN,
            )
            .order_by(Registration.id)
        )
    ).all()
    return [r[0] for r in rows]


async def start_tournament(session: AsyncSession, *, tournament_id: uuid.UUID) -> StartResult:
    """Generate the bracket from checked-in teams and move to RUNNING.

    Idempotency: only a tournament in OPEN/CHECK_IN can be started; a second call
    finds it RUNNING and raises InvalidStateError rather than double-creating.
    """
    tournament = await get_tournament(session, tournament_id=tournament_id)
    if tournament.status not in (TournamentStatus.OPEN, TournamentStatus.CHECK_IN):
        raise InvalidStateError(f"cannot start from state {tournament.status.value}")

    seeds = await _seeded_team_ids(session, tournament_id)
    if len(seeds) < 2:
        raise NotEnoughTeamsError(f"need >=2 checked-in teams, got {len(seeds)}")

    guild_id = await _current_guild(session)
    matches = plan_bracket_matches(
        tournament_id=tournament_id, guild_id=guild_id, seeded_team_ids=seeds
    )
    session.add_all(matches)
    tournament.status = TournamentStatus.RUNNING
    await session.flush()

    rounds = max(m.round for m in matches)
    byes = sum(1 for m in matches if m.status == MatchStatus.WALKOVER)
    return StartResult(
        tournament_id=str(tournament_id),
        matches_created=len(matches),
        rounds=rounds,
        byes=byes,
    )
