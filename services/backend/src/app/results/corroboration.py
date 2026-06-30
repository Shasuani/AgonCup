"""Automatic result corroboration: forgeable reports -> decided winner (PLAN.md §3.6.6).

The "wire" between the persisted, forgeable :class:`ResultReport` rows and the
shared, unit-tested :func:`lol_core.adjudication.adjudicate_result` brain. It runs
**inline at report ingest** (called from the realtime hub right after a report is
persisted), so the moment a quorum of agreeing reports lands the winner is recorded
and the bracket advances — no human in the loop for the clean case.

Why here and not the worker tier: the worker is still a skeleton with no DB engine;
the hub already holds a tenant-scoped session (``app.current_guild`` GUC set at
subscribe) plus :func:`app.results.service.record_winner`, so corroboration runs
where the reports land and where the bracket-advance logic lives.

Flow (PLAN.md §3.6.6):

1. Skip if the match is already decided (``status = COMPLETED``) — idempotent.
2. Build ``roster_by_team`` (``team_id -> {puuid}``) from the **check-in**
   (:class:`ClientSession`) joined to **team membership** — NEVER from the reports.
3. For each report, derive the *claimed* winning team id from the eog
   ``isWinningTeam`` block mapped through the roster (the persisted row leaves
   ``claimed_winner_team`` NULL — that mapping is this layer's job).
4. Adjudicate. ``ACCEPT`` -> :func:`record_winner` (``decided_by = QUORUM``) which
   advances the bracket; ``DISPUTE`` -> do NOT advance (log; admin override is the
   backstop); ``INSUFFICIENT`` -> do nothing (more reports may still arrive — the
   next ingest re-runs this).

Trust note: every report is a forgeable blob. The winner is the agreement of
>=6/10, never any single row. A lone forged "I won" is out-voted; a genuine
conflict freezes (never auto-advances a wrong winner).
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from lol_core.adjudication import AdjudicationOutcome, adjudicate_result
from lol_schemas import ResultReportDTO
from lol_schemas.enums import MatchStatus, ResultDecidedBy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import ClientSession, Match, MatchResult, ResultReport, TeamMember
from app.results import service as results_service

logger = get_logger(__name__)


class _RecordWinner(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        match_id: uuid.UUID,
        winner_team_id: uuid.UUID,
        decided_by: ResultDecidedBy,
    ) -> MatchResult: ...


def _winning_puuids(raw_payload: Any) -> set[str]:
    """The PUUIDs on the eog team flagged ``isWinningTeam`` (forgeable, best-effort).

    Returns an empty set for a malformed / missing payload — such a report simply
    casts no winner vote rather than poisoning adjudication.
    """
    out: set[str] = set()
    if not isinstance(raw_payload, dict):
        return out
    teams = raw_payload.get("teams")
    if not isinstance(teams, list):
        return out
    for team in teams:
        if not isinstance(team, dict) or team.get("isWinningTeam") is not True:
            continue
        for player in team.get("players") or []:
            if isinstance(player, dict) and player.get("puuid"):
                out.add(str(player["puuid"]))
    return out


def _derive_winner_team(
    winning_puuids: set[str], roster_by_team: dict[uuid.UUID, frozenset[str]]
) -> uuid.UUID | None:
    """Map a report's winning-team PUUIDs to OUR team id via the registered roster.

    The team whose roster contains the most of the report's winning PUUIDs wins the
    mapping; a tie or no overlap yields ``None`` (no winner vote). This is how a
    client-side "team 100 won" becomes "team ``<uuid>`` won" without trusting the
    report to name our team id.
    """
    best: uuid.UUID | None = None
    best_n = 0
    tie = False
    for team_id, roster in roster_by_team.items():
        n = len(winning_puuids & roster)
        if n > best_n:
            best_n, best, tie = n, team_id, False
        elif n == best_n and n > 0:
            tie = True
    return None if best_n == 0 or tie else best


async def _roster_by_team(session: AsyncSession, match: Match) -> dict[uuid.UUID, frozenset[str]]:
    """Build ``team_id -> {puuid}`` from this match's check-ins + team membership.

    Sourced from registration/check-in, never the reports (PLAN.md §3.6.6). Each
    checked-in :class:`ClientSession` (``user_id``, ``puuid``) is bound to whichever
    of the match's two teams the user is a member of.
    """
    team_of_user: dict[uuid.UUID, uuid.UUID] = {}
    for team_id in (match.team_a, match.team_b):
        if team_id is None:
            continue
        members = (
            (await session.execute(select(TeamMember.user_id).where(TeamMember.team_id == team_id)))
            .scalars()
            .all()
        )
        for user_id in members:
            team_of_user[user_id] = team_id

    rosters: dict[uuid.UUID, set[str]] = {}
    if match.team_a is not None:
        rosters[match.team_a] = set()
    if match.team_b is not None:
        rosters[match.team_b] = set()
    sessions = (
        await session.execute(
            select(ClientSession.user_id, ClientSession.puuid).where(
                ClientSession.match_id == match.id
            )
        )
    ).all()
    for user_id, puuid in sessions:
        team_id = team_of_user.get(user_id)
        if team_id is not None and team_id in rosters and puuid:
            rosters[team_id].add(str(puuid))
    return {team_id: frozenset(puuids) for team_id, puuids in rosters.items()}


async def adjudicate_match(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    guild_id: uuid.UUID,
    record_winner: _RecordWinner | None = None,
) -> AdjudicationOutcome | None:
    """Adjudicate ``match_id`` from its persisted reports; auto-record on quorum.

    Returns the :class:`AdjudicationOutcome`, or ``None`` when adjudication was
    skipped (match missing, already decided, or a team slot still empty). Safe to
    call after every report ingest: idempotent once a winner is recorded.

    ``record_winner`` is an injectable seam (defaults to
    :func:`app.results.service.record_winner`) so the wiring is unit-testable
    without driving the full bracket-advance path.
    """
    record = record_winner or results_service.record_winner

    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None or match.status == MatchStatus.COMPLETED:
        return None
    if match.team_a is None or match.team_b is None:
        return None

    roster_by_team = await _roster_by_team(session, match)
    if any(len(p) == 0 for p in roster_by_team.values()) or len(roster_by_team) != 2:
        # Not enough of either side has checked in yet to represent both teams.
        return AdjudicationOutcome.INSUFFICIENT

    rows = (
        (await session.execute(select(ResultReport).where(ResultReport.match_id == match_id)))
        .scalars()
        .all()
    )
    dtos = [
        ResultReportDTO(
            guild_id=guild_id,
            match_id=match_id,
            lcu_game_id=r.lcu_game_id,
            reporter_puuid=r.reporter_puuid,
            source=r.source,
            winner=_derive_winner_team(_winning_puuids(r.raw_payload), roster_by_team),
            schema_version=r.schema_version,
            received_at=r.received_at,
        )
        for r in rows
    ]

    verdict = adjudicate_result(dtos, roster_by_team)

    if verdict.outcome == AdjudicationOutcome.ACCEPT and verdict.winner_team_id is not None:
        try:
            await record(
                session,
                match_id=match_id,
                winner_team_id=verdict.winner_team_id,
                decided_by=ResultDecidedBy.QUORUM,
            )
            logger.info(
                "corroboration.accepted",
                match_id=str(match_id),
                winner_team_id=str(verdict.winner_team_id),
                reports_counted=verdict.reports_counted,
            )
        except results_service.ResultError as exc:
            # A racing admin override / conflicting decision — never crash ingest.
            logger.warning("corroboration.record_skipped", match_id=str(match_id), error=str(exc))
    elif verdict.outcome == AdjudicationOutcome.DISPUTE:
        logger.warning(
            "corroboration.disputed",
            match_id=str(match_id),
            anomaly_flags=verdict.anomaly_flags,
            detail=verdict.detail,
        )
    else:
        logger.info(
            "corroboration.insufficient",
            match_id=str(match_id),
            reports_counted=verdict.reports_counted,
        )
    return verdict.outcome


__all__ = ["adjudicate_match"]
