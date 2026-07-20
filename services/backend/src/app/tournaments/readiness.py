"""Match-ready gate — the 5-minute ready window before a match is played.

Edge-cases §8.5 / §0.2. Once a bracket match has **both teams present** it becomes
*playable*: a ready window is armed (:data:`Match.ready_deadline_at`) and each
side's captain marks their team ready (:func:`mark_team_ready`). The periodic
:func:`run_readiness_tick` (driven from the app lifespan loop, same owner session
as the scheduled-tournament ticker) does two things each pass:

* **Arm** — a newly-playable match (both teams, no deadline yet) gets
  ``ready_deadline_at = now + window`` and a "ready up" announcement.
* **Resolve** — at the deadline: a side that readied while its opponent did NOT
  wins by **walkover** (edge-cases §8.5); if *neither* readied the window is
  re-armed with a no-show warning (nobody is waiting, so we never auto-forfeit
  both — an admin cancels a truly dead match).

When both sides ready *before* the deadline the window is disarmed
(:data:`Match.ready_deadline_at` cleared) and a ``match_ready`` signal is emitted;
the autonomous lobby orchestration (Task #3) picks it up from there.

Pure over the session it is given (unit-tested with a fake session); all times are
UTC and supplied by the caller so the pass is deterministic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from lol_schemas.enums import MatchStatus, TeamMemberRole, TeamMemberStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Match, Team, TeamMember, Tournament
from app.notifications import (
    NotificationPublisher,
    publish_tournament_announced,
)

logger = get_logger(__name__)


class ReadinessError(Exception):
    """Base class for match-ready failures."""


class MatchNotFoundError(ReadinessError):
    """No match with that id in the acting guild."""


class TeamNotInMatchError(ReadinessError):
    """The team is not one of the match's two sides."""


class MatchNotReadyableError(ReadinessError):
    """The match is not in a state where a team can ready up (missing a team, or
    already decided)."""


class NotTeamCaptainError(ReadinessError):
    """The caller does not captain either of the match's two teams."""


@dataclass(frozen=True, slots=True)
class ReadyOutcome:
    """Result of :func:`mark_team_ready`."""

    side: str  # "team_a" | "team_b"
    both_ready: bool
    already: bool  # this side had already readied (idempotent no-op)


def _side_of(match: Match, team_id: uuid.UUID) -> str:
    if match.team_a == team_id:
        return "team_a"
    if match.team_b == team_id:
        return "team_b"
    raise TeamNotInMatchError(f"{team_id} is not in match {match.id}")


def _match_label(match: Match) -> str:
    """Human bracket label, e.g. ``Round 2 · Match 3`` (1-indexed slot)."""
    return f"Round {match.round + 1} · Match {match.bracket_slot + 1}"


async def resolve_ready_team(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Return which of the match's two teams ``user_id`` may ready up.

    The caller must be the **active captain** of ``team_a`` or ``team_b`` (the team
    leader presses ready — edge-cases §0.1). Raises :class:`MatchNotFoundError` or
    :class:`NotTeamCaptainError`.
    """
    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    sides = [t for t in (match.team_a, match.team_b) if t is not None]
    if not sides:
        raise MatchNotReadyableError(f"match {match_id} does not yet have teams")
    rows = (
        (
            await session.execute(
                select(TeamMember).where(
                    TeamMember.user_id == user_id,
                    TeamMember.role == TeamMemberRole.CAPTAIN,
                    TeamMember.status == TeamMemberStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )
    for tm in rows:
        if tm.team_id in sides:
            return tm.team_id
    raise NotTeamCaptainError(f"{user_id} captains neither team in match {match_id}")


async def mark_team_ready(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    team_id: uuid.UUID,
    now: datetime,
) -> ReadyOutcome:
    """Mark ``team_id``'s side of ``match_id`` as ready (idempotent).

    Raises :class:`MatchNotFoundError`, :class:`TeamNotInMatchError`, or
    :class:`MatchNotReadyableError`. When this call makes *both* sides ready the
    ready window is disarmed (``ready_deadline_at`` cleared) — the match is handed
    off to lobby orchestration; ``both_ready`` is returned ``True`` so the caller
    can emit the match-ready signal.
    """
    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    if match.team_a is None or match.team_b is None:
        raise MatchNotReadyableError(f"match {match_id} does not yet have both teams")
    if match.status not in (MatchStatus.PENDING, MatchStatus.SCHEDULED):
        raise MatchNotReadyableError(f"match {match_id} is not awaiting play")

    side = _side_of(match, team_id)
    attr = f"{side}_ready_at"
    already = getattr(match, attr) is not None
    if not already:
        setattr(match, attr, now)

    both_ready = match.team_a_ready_at is not None and match.team_b_ready_at is not None
    if both_ready:
        match.ready_deadline_at = None  # disarm — no forfeit risk once both readied.
    await session.flush()
    logger.info(
        "match_ready.marked",
        match_id=str(match_id),
        side=side,
        both_ready=both_ready,
        already=already,
    )
    return ReadyOutcome(side=side, both_ready=both_ready, already=already)


async def _team_names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = (await session.execute(select(Team.id, Team.name).where(Team.id.in_(ids)))).all()
    return {r[0]: r[1] for r in rows}


async def run_readiness_tick(
    session: AsyncSession,
    publisher: NotificationPublisher,
    *,
    now: datetime,
    window_seconds: int,
) -> tuple[int, int]:
    """One readiness pass. Returns ``(armed, forfeited)`` counts.

    See the module docstring. Cross-tenant: must run on the OWNER session (RLS
    bypassed) so it can see every guild's matches; ``origin_guild_id`` on each
    announcement is read back from the match row.
    """
    window = timedelta(seconds=window_seconds)

    # --- Arm pass: newly-playable matches (both teams, no deadline yet). --------
    armed = 0
    to_arm = (
        (
            await session.execute(
                select(Match).where(
                    Match.status == MatchStatus.PENDING,
                    Match.team_a.is_not(None),
                    Match.team_b.is_not(None),
                    Match.ready_deadline_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    # Defensive re-check in Python (keeps the pass correct even over a store that
    # can't model the IS NULL / IS NOT NULL predicates — e.g. the unit fake session).
    # A disarmed both-ready match keeps ready_deadline_at NULL but is not re-armed.
    to_arm = [
        m
        for m in to_arm
        if m.status == MatchStatus.PENDING
        and m.team_a is not None
        and m.team_b is not None
        and m.ready_deadline_at is None
        and not (m.team_a_ready_at is not None and m.team_b_ready_at is not None)
    ]

    # --- Resolve pass: matches whose window has elapsed. -----------------------
    forfeited = 0
    to_resolve = (
        (
            await session.execute(
                select(Match).where(
                    Match.status == MatchStatus.PENDING,
                    Match.ready_deadline_at.is_not(None),
                    Match.ready_deadline_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    to_resolve = [
        m
        for m in to_resolve
        if m.status == MatchStatus.PENDING
        and m.ready_deadline_at is not None
        and m.ready_deadline_at <= now
    ]

    # Batch-resolve display names for every team we might announce.
    name_ids: set[uuid.UUID] = set()
    for m in [*to_arm, *to_resolve]:
        if m.team_a is not None:
            name_ids.add(m.team_a)
        if m.team_b is not None:
            name_ids.add(m.team_b)
    names = await _team_names(session, name_ids)
    tour_names = {
        r[0]: r[1]
        for r in (
            await session.execute(
                select(Tournament.id, Tournament.name).where(
                    Tournament.id.in_({m.tournament_id for m in [*to_arm, *to_resolve]})
                )
            )
        ).all()
    }

    for m in to_arm:
        m.ready_deadline_at = now + window
        a = names.get(m.team_a, "TBD") if m.team_a else "TBD"
        b = names.get(m.team_b, "TBD") if m.team_b else "TBD"
        mins = window_seconds // 60
        await publish_tournament_announced(
            publisher,
            origin_guild_id=str(m.guild_id),
            tournament_name=tour_names.get(m.tournament_id, "Tournament"),
            headline=f"{_match_label(m)} — ready up",
            detail=f"**{a}** vs **{b}**. Both captains have {mins} min to ready up.",
        )
        armed += 1

    # Local import to avoid a module-load cycle (results.service imports models too).
    from lol_schemas.enums import ResultDecidedBy

    from app.results.service import record_winner

    for m in to_resolve:
        a_ready = m.team_a_ready_at is not None
        b_ready = m.team_b_ready_at is not None
        if a_ready and b_ready:
            m.ready_deadline_at = None  # both made it — hand off to orchestration.
            continue
        if a_ready == b_ready:  # neither readied → re-arm, warn (no double-forfeit).
            m.ready_deadline_at = now + window
            await publish_tournament_announced(
                publisher,
                origin_guild_id=str(m.guild_id),
                tournament_name=tour_names.get(m.tournament_id, "Tournament"),
                headline=f"{_match_label(m)} — waiting",
                detail="Neither team has readied up yet. The ready window was extended.",
            )
            continue
        # Exactly one side readied → the other forfeits by walkover.
        winner_id = m.team_a if a_ready else m.team_b
        loser_id = m.team_b if a_ready else m.team_a
        assert winner_id is not None  # xor guarantees the ready side has a team
        m.ready_deadline_at = None
        await record_winner(
            session,
            match_id=m.id,
            winner_team_id=winner_id,
            decided_by=ResultDecidedBy.WALKOVER,
        )
        await publish_tournament_announced(
            publisher,
            origin_guild_id=str(m.guild_id),
            tournament_name=tour_names.get(m.tournament_id, "Tournament"),
            headline=f"{_match_label(m)} — walkover",
            detail=(
                f"**{names.get(winner_id, 'A team')}** advances — "
                f"**{names.get(loser_id, 'the opponent') if loser_id else 'the opponent'}** "
                "did not ready up in time."
            ),
        )
        forfeited += 1

    await session.flush()
    return armed, forfeited


__all__ = [
    "MatchNotFoundError",
    "MatchNotReadyableError",
    "NotTeamCaptainError",
    "ReadinessError",
    "ReadyOutcome",
    "TeamNotInMatchError",
    "mark_team_ready",
    "resolve_ready_team",
    "run_readiness_tick",
]
