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
   ``claimed_winner_team`` NULL — that mapping is this layer's job). A
   first-blood mode (``GameMode.decides_on_first_blood``, the 1v1 ARAM duel)
   instead votes by the report's own kill-stream capture — the duel is won at
   first blood, the eog winner bit is only the fallback (``_claimed_winner``).
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

from lol_core.adjudication import AdjudicationOutcome, adjudicate_result, derive_first_blood_team
from lol_schemas import ResultReportDTO
from lol_schemas.enums import GameMode, MatchStatus, ResultDecidedBy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit_service
from app.logging import get_logger
from app.models import (
    ClientSession,
    Match,
    MatchResult,
    ResultReport,
    RiotAccount,
    TeamMember,
    Tournament,
)
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


class _MarkDisputed(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        match_id: uuid.UUID,
        agreed_game_id: str | None,
        anomaly_flags: list[str],
        reports_counted: int,
        both_teams_represented: bool,
    ) -> MatchResult | None: ...


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


def _kill_stream_events(kill_stream: Any) -> list[dict[str, Any]]:
    """The camelCase event list out of a persisted ``kill_stream`` JSONB blob."""
    if not isinstance(kill_stream, dict):
        return []
    events = kill_stream.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict)]


async def _puuid_by_name(
    session: AsyncSession, roster_by_team: dict[uuid.UUID, frozenset[str]]
) -> dict[str, str]:
    """Normalized live-client display name -> roster PUUID, for first-blood votes.

    Riot ID display fields are refreshable and NEVER identity (:class:`RiotAccount`);
    here they only translate a forgeable kill-stream name into one winner *vote*,
    which corroboration still has to agree on across reports (§3.6.6). Keys are
    ``casefold().strip()``-normalized: the bare game name plus ``name#tag``. A
    name shared by two roster PUUIDs is dropped rather than guessed.
    """
    puuids = sorted({p for roster in roster_by_team.values() for p in roster})
    if not puuids:
        return {}
    rows = (
        await session.execute(
            select(
                RiotAccount.puuid, RiotAccount.riot_id_game_name, RiotAccount.riot_id_tag_line
            ).where(RiotAccount.puuid.in_(puuids))
        )
    ).all()
    mapping: dict[str, str] = {}
    collided: set[str] = set()
    for puuid, game_name, tag_line in rows:
        if not game_name or not game_name.strip():
            continue
        base = game_name.casefold().strip()
        keys = {base}
        if tag_line and tag_line.strip():
            keys.add(f"{base}#{tag_line.casefold().strip()}")
        for key in keys:
            if key in collided:
                continue
            if key in mapping and mapping[key] != str(puuid):
                del mapping[key]
                collided.add(key)
                continue
            mapping[key] = str(puuid)
    return mapping


def _claimed_winner(
    report: ResultReport,
    roster_by_team: dict[uuid.UUID, frozenset[str]],
    first_blood_names: dict[str, str] | None,
) -> uuid.UUID | None:
    """One report's winner vote (flow step 3).

    In a first-blood mode (``GameMode.decides_on_first_blood`` — the 1v1 ARAM
    duel) the vote derives from the report's own kill-stream capture: the duel is
    won at first blood, and the eog ``isWinningTeam`` bit (whoever outlasted the
    other after the duelists leave) is only the fallback when the capture is
    missing or unmappable. Every other mode votes by the eog winner as before.
    """
    if first_blood_names is not None:
        vote = derive_first_blood_team(
            _kill_stream_events(report.kill_stream), first_blood_names, roster_by_team
        )
        if vote is not None:
            return vote
    return _derive_winner_team(_winning_puuids(report.raw_payload), roster_by_team)


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
    mark_disputed: _MarkDisputed | None = None,
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
    mark = mark_disputed or results_service.mark_disputed

    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None or match.status == MatchStatus.COMPLETED:
        return None
    if match.team_a is None or match.team_b is None:
        return None

    roster_by_team = await _roster_by_team(session, match)
    if any(len(p) == 0 for p in roster_by_team.values()) or len(roster_by_team) != 2:
        # Not enough of either side has checked in yet to represent both teams.
        return AdjudicationOutcome.INSUFFICIENT

    # The tournament's mode drives quorum (owner rule: ONE report decides a 1v1
    # duel; a 5v5 needs two agreeing reports, one from each team); its best_of
    # drives how many game wins decide the match (a series spans several LCU
    # games, each adjudicated on its own).
    meta = (
        await session.execute(
            select(Tournament.game_mode, Tournament.best_of).where(
                Tournament.id == match.tournament_id
            )
        )
    ).first()
    game_mode = (meta[0] if meta else None) or GameMode.SUMMONERS_RIFT_5V5
    best_of = meta[1] if meta and meta[1] else 1
    needed = best_of // 2 + 1

    # First-blood modes vote by kill stream; the name->PUUID map is per-match.
    first_blood_names: dict[str, str] | None = None
    if game_mode.decides_on_first_blood:
        first_blood_names = await _puuid_by_name(session, roster_by_team)

    rows = (
        (await session.execute(select(ResultReport).where(ResultReport.match_id == match_id)))
        .scalars()
        .all()
    )
    by_game: dict[str, list[Any]] = {}
    for r in rows:
        by_game.setdefault(r.lcu_game_id, []).append(r)

    # Game ids OTHER matches in this tournament already consumed: an eog block
    # replayed from a different match must trip FLAG_REUSED_GAME_ID (dispute),
    # never score. This match's own scored games are skipped by the loop below.
    sibling_ids = (
        (
            await session.execute(
                select(Match.scored_game_ids).where(
                    Match.tournament_id == match.tournament_id, Match.id != match_id
                )
            )
        )
        .scalars()
        .all()
    )
    used_elsewhere = frozenset(gid for ids in sibling_ids for gid in (ids or []))

    scored = list(match.scored_game_ids or [])
    scored_set = set(scored)
    disputed = False

    # Score each not-yet-counted game once. A re-sent report for a scored game is a
    # no-op (game id already in ``scored``), so wins never double-count.
    for game_id in sorted(g for g in by_game if g not in scored_set):
        dtos = [
            ResultReportDTO(
                guild_id=guild_id,
                match_id=match_id,
                lcu_game_id=r.lcu_game_id,
                reporter_puuid=r.reporter_puuid,
                source=r.source,
                winner=_claimed_winner(r, roster_by_team, first_blood_names),
                schema_version=r.schema_version,
                received_at=r.received_at,
                # per_player_kda is DELIBERATELY not wired (the data is in
                # ResultReport.reported_kda): the eog scoreboard is deterministic,
                # so all honest clients produce byte-identical KDA blocks — and
                # lol-core's byte-identical-payload detector would then mark every
                # reporter suspect and dispute every honest match. Wiring it needs
                # a detector redesign first (owner decision).
            )
            for r in by_game[game_id]
        ]
        verdict = adjudicate_result(
            dtos,
            roster_by_team,
            quorum_threshold=game_mode.quorum_threshold,
            total_participants=game_mode.total_participants,
            require_both_teams=game_mode.requires_both_teams,
            used_game_ids=used_elsewhere,
        )
        if verdict.outcome == AdjudicationOutcome.ACCEPT and verdict.winner_team_id is not None:
            if verdict.winner_team_id == match.team_a:
                match.wins_a += 1
            elif verdict.winner_team_id == match.team_b:
                match.wins_b += 1
            scored.append(game_id)
            scored_set.add(game_id)
            logger.info(
                "corroboration.game_decided",
                match_id=str(match_id),
                game_id=game_id,
                winner_team_id=str(verdict.winner_team_id),
                wins_a=match.wins_a,
                wins_b=match.wins_b,
            )
        elif verdict.outcome == AdjudicationOutcome.DISPUTE:
            disputed = True
            try:
                await mark(
                    session,
                    match_id=match_id,
                    agreed_game_id=verdict.agreed_game_id,
                    anomaly_flags=verdict.anomaly_flags,
                    reports_counted=verdict.reports_counted,
                    both_teams_represented=verdict.both_teams_represented,
                )
                await audit_service.record_audit(
                    session,
                    action="result.disputed",
                    entity_type="match",
                    entity_id=match_id,
                    actor_kind=audit_service.ACTOR_SYSTEM,
                    guild_id=guild_id,
                    detail=", ".join(verdict.anomaly_flags) or "conflicting reports",
                )
            except results_service.ResultError as exc:
                logger.warning(
                    "corroboration.dispute_skipped", match_id=str(match_id), error=str(exc)
                )
            logger.warning("corroboration.disputed", match_id=str(match_id), game_id=game_id)
            break  # freeze the series for admin review
        # INSUFFICIENT: this game's reports aren't all in yet — wait for the rest.

    # Persist the updated series state (reassign so SQLAlchemy tracks the JSONB list).
    match.scored_game_ids = scored

    if disputed:
        return AdjudicationOutcome.DISPUTE

    series_winner: uuid.UUID | None = None
    if match.wins_a >= needed:
        series_winner = match.team_a
    elif match.wins_b >= needed:
        series_winner = match.team_b
    if series_winner is None:
        return AdjudicationOutcome.INSUFFICIENT  # series still in progress

    try:
        await record(
            session,
            match_id=match_id,
            winner_team_id=series_winner,
            decided_by=ResultDecidedBy.QUORUM,
        )
        await audit_service.record_audit(
            session,
            action="result.quorum",
            entity_type="match",
            entity_id=match_id,
            actor_kind=audit_service.ACTOR_SYSTEM,
            guild_id=guild_id,
            detail=f"series decided {match.wins_a}-{match.wins_b} (best of {best_of})",
        )
        logger.info(
            "corroboration.series_decided",
            match_id=str(match_id),
            winner_team_id=str(series_winner),
            wins_a=match.wins_a,
            wins_b=match.wins_b,
        )
    except results_service.ResultError as exc:
        # A racing admin override / conflicting decision — never crash ingest.
        logger.warning("corroboration.record_skipped", match_id=str(match_id), error=str(exc))
    return AdjudicationOutcome.ACCEPT


__all__ = ["adjudicate_match"]
