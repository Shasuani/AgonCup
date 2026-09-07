"""Match result recording and single-elimination bracket advancement.

This is the *decided-result* half of the results module (PLAN.md §3.6.6 / §2
``MatchResult``). The quorum/anomaly ingress (the 10 forgeable per-participant
reports) lands separately; this service records the **decided** winner of a match
— either an admin override or an already-adjudicated quorum/walkover outcome —
and propagates it through the bracket.

Recording a winner does three things, atomically on the tenant session
(PLAN.md §2, the ``MatchResult``/``Match`` rows):

1. **Upsert the ``MatchResult``** keyed by ``UNIQUE(match_id)`` (idempotent: a
   re-report of the same winner is a no-op update, not a duplicate row).
2. **Stamp the ``Match``**: ``winner_team_id`` + ``status = COMPLETED``.
3. **Advance** the winner into the next round's placeholder ``Match`` — the slot
   given by the bracket layout (winner of round ``r`` slot ``s`` feeds round
   ``r + 1`` slot ``s // 2``; even ``s`` fills ``team_a``, odd ``s`` fills
   ``team_b``). The advance is a **conditional, idempotent** fill: it only writes
   an *empty* slot, never overwrites an already-present team, so replaying a
   report or racing two reports cannot corrupt the next match. When both sides of
   the next match become present it flips ``PENDING`` (ready to be played). The
   **final** has no next match — advancement is a no-op there.

Byes auto-advance: ``plan_bracket_matches`` pre-fills bye matches as ``WALKOVER``
with a winner already set, so :func:`advance_bye_chain` walks those forward at
start time without an admin call (a bye whose next-round opponent is also a bye
cascades to the round after that).

RLS-safe: every read/write goes through the tenant session whose
``app.current_guild`` GUC is already set by ``get_tenant_session``, so a match in
another guild is invisible and the conditional next-slot update is scoped to the
acting tenant. The next-round lookup is additionally pinned to the same
``tournament_id`` so it can never reach across tournaments.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lol_schemas.enums import (
    MatchResultState,
    MatchStatus,
    ResultDecidedBy,
    TournamentStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Match, MatchResult, ResultReport, RiotAccount, Team, Tournament, User

logger = get_logger(__name__)

# A bye match advances its pre-filled winner without an admin call; a real match
# only advances once a winner is decided (status COMPLETED/WALKOVER/FORFEIT).
_DECIDED_STATUSES = frozenset({MatchStatus.COMPLETED, MatchStatus.WALKOVER, MatchStatus.FORFEIT})


class ResultError(Exception):
    """Base class for result-recording failures."""


class MatchNotFoundError(ResultError):
    """No match with that id in the acting guild (RLS-scoped)."""


class TeamNotInMatchError(ResultError):
    """The declared winner is neither ``team_a`` nor ``team_b`` of the match."""


class MatchNotReadyError(ResultError):
    """Both teams are not yet present, so no winner can be declared."""


class ResultConflictError(ResultError):
    """A different winner was already decided (no silent re-flip).

    Changing a decided winner after the bracket has advanced would desync the
    ``Match`` row from the successor slot, so it is rejected; correcting a result
    needs an explicit revise path that un-advances the prior winner first.
    """


class ResultAlreadyDecidedError(ResultError):
    """An ADMIN_OVERRIDE decision is terminal — a non-override (e.g. a later
    quorum) call cannot overwrite it."""


# ---------------------------------------------------------------------------
# Pure bracket-geometry helpers (no DB — exhaustively unit-tested)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NextSlot:
    """Where the winner of (``round``, ``slot``) lands in the following round."""

    round: int
    slot: int
    is_team_a: bool


def next_slot_for(round_no: int, slot: int) -> NextSlot:
    """Map a match's (round, slot) to its successor placeholder (PLAN.md §2 layout).

    Winner of round ``r`` slot ``s`` feeds round ``r + 1`` slot ``s // 2``; an even
    source slot fills the next match's ``team_a`` (top), an odd one fills
    ``team_b`` (bottom). Pure and total — the caller decides whether a successor
    actually exists (the final has none).
    """
    return NextSlot(round=round_no + 1, slot=slot // 2, is_team_a=slot % 2 == 0)


def place_winner_in_slot(next_match: Match, winner_team_id: uuid.UUID, *, is_team_a: bool) -> bool:
    """Conditionally fill the winner into the target side of ``next_match``.

    Idempotent and non-destructive: returns ``True`` only if it actually wrote an
    **empty** slot. If the slot already holds the same team (a replay) or a
    different team (a race already resolved), it leaves the row untouched and
    returns ``False`` — advancement can be retried freely. When the write makes
    both sides present, the placeholder flips ``PENDING`` (ready to play).
    """
    side = "team_a" if is_team_a else "team_b"
    current = getattr(next_match, side)
    if current is not None:
        # Already filled (same team = replay, different = race resolved) -> no-op.
        return False
    setattr(next_match, side, winner_team_id)
    if next_match.team_a is not None and next_match.team_b is not None:
        next_match.status = MatchStatus.PENDING
    return True


# ---------------------------------------------------------------------------
# Async recording + advancement (tenant-scoped via RLS)
# ---------------------------------------------------------------------------
async def _get_match(session: AsyncSession, match_id: uuid.UUID) -> Match:
    """Load a match (RLS already scopes it to the acting guild)."""
    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    return match


async def _find_next_match(session: AsyncSession, match: Match) -> Match | None:
    """Locate the placeholder ``Match`` the winner of ``match`` advances into.

    Returns ``None`` for the final (no row at round + 1). Pinned to the same
    tournament so advancement can never cross tournaments; RLS pins the guild.
    """
    target = next_slot_for(match.round, match.bracket_slot)
    return (
        await session.execute(
            select(Match).where(
                Match.tournament_id == match.tournament_id,
                Match.round == target.round,
                Match.bracket_slot == target.slot,
            )
        )
    ).scalar_one_or_none()


async def _advance_from(session: AsyncSession, match: Match) -> Match | None:
    """Push ``match``'s winner into its successor placeholder (idempotent).

    Returns the next match if it now has both teams present (so a caller can chain
    bye cascades), else ``None``. A no-op when ``match`` has no winner, has no
    successor (final), or the successor slot is already filled.
    """
    if match.winner_team_id is None:
        return None
    next_match = await _find_next_match(session, match)
    if next_match is None:
        return None  # final — nothing to advance into.
    target = next_slot_for(match.round, match.bracket_slot)
    place_winner_in_slot(next_match, match.winner_team_id, is_team_a=target.is_team_a)
    return next_match if next_match.status == MatchStatus.PENDING else None


async def _complete_if_finished(session: AsyncSession, *, tournament_id: uuid.UUID) -> bool:
    """Flip a RUNNING tournament to COMPLETED once every match is decided.

    "Decided" is the same set the bracket advances on — played, walkover, or
    forfeit — so a bracket full of byes closes as readily as one played out.
    Idempotent, and it never touches a tournament that is not RUNNING (a
    cancelled one stays cancelled).
    """
    tournament = (
        await session.execute(select(Tournament).where(Tournament.id == tournament_id))
    ).scalar_one_or_none()
    if tournament is None or tournament.status is not TournamentStatus.RUNNING:
        return False

    matches = list(
        (await session.execute(select(Match).where(Match.tournament_id == tournament_id)))
        .scalars()
        .all()
    )
    if not matches or any(m.status not in _DECIDED_STATUSES for m in matches):
        return False

    tournament.status = TournamentStatus.COMPLETED
    await session.flush()
    logger.info("tournament.completed", tournament_id=str(tournament_id))
    return True


async def advance_bye_chain(session: AsyncSession, match: Match) -> None:
    """Walk a decided ``match`` forward, cascading through bye-vs-bye placeholders.

    Used at bracket-start for the pre-filled ``WALKOVER`` byes: a bye advances into
    the next placeholder, and if that placeholder is *itself* a bye (its only other
    feeder was also a bye, so it carries a single team and a pre-decided winner)
    the walk continues. The conditional fill keeps every hop idempotent.

    The cascade stops when the successor becomes a real two-team match (it now
    needs to be played), when it is still waiting on a second feeder, or at the
    final (no successor).
    """
    cursor: Match | None = match
    while cursor is not None and cursor.winner_team_id is not None:
        next_match = await _find_next_match(session, cursor)
        if next_match is None:
            break  # final reached.
        target = next_slot_for(cursor.round, cursor.bracket_slot)
        place_winner_in_slot(next_match, cursor.winner_team_id, is_team_a=target.is_team_a)
        await session.flush()
        # Continue only while the successor is itself an already-decided bye
        # (single team, pre-filled winner); a real match stops the cascade.
        cursor = next_match if next_match.winner_team_id is not None else None


async def record_winner(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    winner_team_id: uuid.UUID,
    decided_by: ResultDecidedBy = ResultDecidedBy.ADMIN_OVERRIDE,
    admin_override_by: uuid.UUID | None = None,
    admin_override_reason: str | None = None,
) -> MatchResult:
    """Record a match's decided winner and advance it through the bracket.

    Steps (PLAN.md §3.6.6): upsert the ``MatchResult`` keyed ``UNIQUE(match_id)``,
    stamp ``Match.winner_team_id`` + ``status = COMPLETED``, then conditionally
    advance the winner into the next round's placeholder. Idempotent end-to-end:
    re-recording the same winner re-confirms the existing result and re-runs the
    (no-op) conditional advance rather than duplicating rows or double-advancing.

    Args:
        match_id: the match being decided (must be in the acting guild).
        winner_team_id: must be ``team_a`` or ``team_b`` of that match.
        decided_by: provenance of the decision (admin override by default; quorum
            / walkover / forfeit are also valid for an already-adjudicated call).
        admin_override_by / admin_override_reason: audit fields for an override.

    Raises:
        MatchNotFoundError: no such match in the acting guild.
        MatchNotReadyError: the match does not yet have both teams.
        TeamNotInMatchError: ``winner_team_id`` is not a participant.
    """
    match = await _get_match(session, match_id)

    if match.team_a is None or match.team_b is None:
        raise MatchNotReadyError(f"match {match_id} is missing a team")
    if winner_team_id not in (match.team_a, match.team_b):
        raise TeamNotInMatchError(f"{winner_team_id} is not in match {match_id}")

    is_override = decided_by == ResultDecidedBy.ADMIN_OVERRIDE
    state = MatchResultState.CONFIRMED

    # (a) Upsert MatchResult keyed UNIQUE(match_id) — idempotent on the match id.
    result = (
        await session.execute(select(MatchResult).where(MatchResult.match_id == match_id))
    ).scalar_one_or_none()

    # Re-decision guard (PLAN.md §3.6.6): protect a decided match from silent
    # corruption. Same winner is an idempotent no-op; a DIFFERENT winner is
    # rejected (changing it post-advancement would desync the bracket — that
    # needs an explicit un-advance/revise path); and an ADMIN_OVERRIDE outcome is
    # terminal against any non-override (e.g. a late quorum) call.
    now = datetime.now(UTC)
    if result is not None:
        if result.winner_team_id == winner_team_id:
            logger.info("result.replay_noop", match_id=str(match_id))
            # Closing a finished bracket is a postcondition of this call, not a
            # side effect of inserting a row: a tournament left RUNNING by an
            # earlier build is repaired by re-recording the same winner, which
            # is otherwise the only no-op an admin has.
            await _complete_if_finished(session, tournament_id=match.tournament_id)
            return result
        if (
            is_override
            and result.state == MatchResultState.DISPUTED
            and result.winner_team_id is None
        ):
            # A queued dispute is an OPEN question, not a decision: the admin
            # resolving it UPGRADES the row in place. Without this branch the
            # conflict guard below 409'd every dispute (winner NULL never equals
            # the proposed winner), making the dispute queue a write-only inbox.
            # Only an override resolves it — a late quorum re-run must not paper
            # over the very conflict that froze the match.
            result.winner_team_id = winner_team_id
            result.decided_by = decided_by
            result.admin_override_by = admin_override_by
            result.admin_override_reason = admin_override_reason
            result.state = MatchResultState.CONFIRMED
            result.decided_at = now
            logger.info("result.dispute_resolved", match_id=str(match_id))
        elif result.decided_by == ResultDecidedBy.ADMIN_OVERRIDE and not is_override:
            raise ResultAlreadyDecidedError(
                f"match {match_id} decided by admin override; cannot overwrite"
            )
        else:
            raise ResultConflictError(
                f"match {match_id} already decided for a different winner; revise required"
            )
    else:
        # No decided result exists for this match → insert one.
        result = MatchResult(
            guild_id=match.guild_id,
            match_id=match_id,
            tournament_id=match.tournament_id,
            winner_team_id=winner_team_id,
            decided_by=decided_by,
            admin_override_by=admin_override_by if is_override else None,
            admin_override_reason=admin_override_reason if is_override else None,
            state=state,
            decided_at=now,
        )
        session.add(result)

    # (b) Stamp the match itself.
    match.winner_team_id = winner_team_id
    match.status = MatchStatus.COMPLETED

    await session.flush()

    # (c) Advance the winner into the next round's placeholder (idempotent).
    await _advance_from(session, match)
    await session.flush()

    # (d) Close the tournament once nothing is left to play. Advancing stops at
    # the final by design (there is no successor slot), so without this a decided
    # bracket sat at RUNNING forever — the champion was crowned and the
    # tournament still looked live to everyone reading the status.
    await _complete_if_finished(session, tournament_id=match.tournament_id)

    logger.info(
        "result.recorded",
        match_id=str(match_id),
        winner_team_id=str(winner_team_id),
        decided_by=decided_by.value,
    )
    return result


# ---------------------------------------------------------------------------
# Disputes — a frozen, un-advanced match awaiting admin override (PLAN.md §3.6.6)
# ---------------------------------------------------------------------------
async def mark_disputed(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    agreed_game_id: str | None,
    anomaly_flags: list[str],
    reports_counted: int,
    both_teams_represented: bool,
) -> MatchResult | None:
    """Record a match as ``DISPUTED`` (no winner, no advance) for the admin queue.

    Called by corroboration when the reports carry an adversarial signal. Never
    downgrades an already-``CONFIRMED`` result (an admin override / clean quorum
    wins); otherwise upserts the ``MatchResult`` in ``DISPUTED`` with the anomaly
    audit trail in ``quorum_detail``. The bracket does NOT advance — the admin
    decides via :func:`record_winner` (admin override).
    """
    match = await _get_match(session, match_id)
    result = (
        await session.execute(select(MatchResult).where(MatchResult.match_id == match_id))
    ).scalar_one_or_none()
    if result is not None and result.state == MatchResultState.CONFIRMED:
        return result  # already decided — do not re-open.

    detail = {
        "anomaly_flags": anomaly_flags,
        "reports_counted": reports_counted,
        "both_teams_represented": both_teams_represented,
    }
    now = datetime.now(UTC)
    if result is None:
        result = MatchResult(
            guild_id=match.guild_id,
            match_id=match_id,
            tournament_id=match.tournament_id,
            winner_team_id=None,
            decided_by=ResultDecidedBy.QUORUM,
            quorum_detail=detail,
            state=MatchResultState.DISPUTED,
            agreed_game_id=agreed_game_id,
            decided_at=now,
        )
        session.add(result)
    else:
        result.state = MatchResultState.DISPUTED
        result.quorum_detail = detail
        result.agreed_game_id = agreed_game_id
        result.decided_at = now
    await session.flush()
    logger.info("result.disputed", match_id=str(match_id), anomaly_flags=anomaly_flags)
    return result


@dataclass(frozen=True, slots=True)
class DisputeTeam:
    team_id: str
    name: str


@dataclass(frozen=True, slots=True)
class DisputeSummary:
    """One frozen match in the admin dispute/override queue (PLAN.md §3.6.6)."""

    match_id: str
    tournament_id: str
    tournament_name: str | None
    round_label: str | None
    status: str
    teams: list[DisputeTeam]
    anomaly_flags: list[str]


async def list_disputes(session: AsyncSession) -> list[DisputeSummary]:
    """Return every ``DISPUTED`` match in the acting guild for the override queue.

    RLS-scoped: only the acting guild's disputed results are visible (tenant
    session). Joins the match + tournament for context and resolves both team
    names so the admin can pick a winner. Anomaly flags come from the stored
    ``quorum_detail`` audit trail written at dispute time.
    """
    rows = (
        await session.execute(
            select(MatchResult, Match, Tournament.name)
            .join(Match, MatchResult.match_id == Match.id)
            .join(Tournament, Match.tournament_id == Tournament.id)
            .where(MatchResult.state == MatchResultState.DISPUTED)
            .order_by(MatchResult.decided_at.desc())
        )
    ).all()
    if not rows:
        return []

    team_ids = {t for _, m, _ in rows for t in (m.team_a, m.team_b) if t is not None}
    names: dict[str, str] = {}
    if team_ids:
        for tid, name in (
            await session.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids)))
        ).all():
            names[str(tid)] = name

    summaries: list[DisputeSummary] = []
    for result, match, tournament_name in rows:
        teams = [
            DisputeTeam(team_id=str(tid), name=names.get(str(tid), "?"))
            for tid in (match.team_a, match.team_b)
            if tid is not None
        ]
        detail = result.quorum_detail if isinstance(result.quorum_detail, dict) else {}
        flags = detail.get("anomaly_flags")
        summaries.append(
            DisputeSummary(
                match_id=str(match.id),
                tournament_id=str(match.tournament_id),
                tournament_name=tournament_name,
                round_label=f"Round {match.round}" if match.round is not None else None,
                status="disputed",
                teams=teams,
                anomaly_flags=list(flags) if isinstance(flags, list) else [],
            )
        )
    return summaries


# ---------------------------------------------------------------------------
# Match history (read model) — per-game scoreboard for the SPA (PLAN.md §3.6.5)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class HistoryPlayer:
    """One scoreboard line: the player's champion + KDA, name resolved if linked.

    ``puuid`` is from the (forgeable) eog block; ``display_name``/``riot_id`` are
    resolved from our ``RiotAccount`` -> ``User`` join and are ``None`` for an
    unlinked participant (we only know their PUUID).
    """

    puuid: str
    display_name: str | None
    riot_id: str | None
    champion_id: int
    kills: int
    deaths: int
    assists: int
    # Scoreboard extras the newer clients capture (PLAN.md §3.6.5); all optional
    # so reports from before they were captured still render.
    champion_name: str | None = None
    level: int | None = None
    items: tuple[int, ...] = ()
    spell1_id: int | None = None
    spell2_id: int | None = None
    gold_earned: int | None = None
    minions_killed: int | None = None
    damage_to_champions: int | None = None
    vision_score: int | None = None
    profile_icon_id: int | None = None


@dataclass(frozen=True, slots=True)
class HistoryTeam:
    is_winner: bool
    players: list[HistoryPlayer]


@dataclass(frozen=True, slots=True)
class HistoryGame:
    """One played game, reconstructed from a representative ``ResultReport`` row."""

    match_id: str
    tournament_id: str
    tournament_name: str | None
    round: int | None
    lcu_game_id: str
    played_at: datetime
    teams: list[HistoryTeam]


def _history_players(
    team_raw: dict[str, Any], names: dict[str, tuple[str | None, str | None]]
) -> list[HistoryPlayer]:
    """Map one eog team's ``players`` block into resolved :class:`HistoryPlayer`s."""
    out: list[HistoryPlayer] = []
    players = team_raw.get("players")
    if not isinstance(players, list):
        return out
    for p in players:
        if not isinstance(p, dict):
            continue
        puuid = str(p.get("puuid") or "")
        display_name, riot_id = names.get(puuid, (None, None))
        raw_items = p.get("items")
        items = (
            tuple(int(i) for i in raw_items if isinstance(i, (int, float)))
            if isinstance(raw_items, list)
            else ()
        )
        out.append(
            HistoryPlayer(
                puuid=puuid,
                display_name=display_name,
                riot_id=riot_id,
                champion_id=int(p.get("championId") or 0),
                kills=int(p.get("championsKilled") or 0),
                deaths=int(p.get("numDeaths") or 0),
                assists=int(p.get("assists") or 0),
                champion_name=_opt_str(p.get("championName")),
                level=_opt_int(p.get("level")),
                items=items,
                spell1_id=_opt_int(p.get("spell1Id")),
                spell2_id=_opt_int(p.get("spell2Id")),
                gold_earned=_opt_int(p.get("goldEarned")),
                minions_killed=_opt_int(p.get("minionsKilled")),
                damage_to_champions=_opt_int(p.get("damageToChampions")),
                vision_score=_opt_int(p.get("visionScore")),
                profile_icon_id=_opt_int(p.get("profileIconId")),
            )
        )
    return out


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


async def list_my_match_history(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = 20
) -> list[HistoryGame]:
    """The caller's own recent games — the player-facing slice of the history.

    A player's desktop client shows "your recent games" (PLAN.md §3.4 client-first):
    the same per-game scoreboards as :func:`list_match_history`, kept only when one
    of the caller's linked ``RiotAccount`` PUUIDs appears on a team. The scan reads
    a wider window than ``limit`` (the tenant's recent games are mostly other
    people's) and trims afterwards. Same RLS scope and the same forgeable,
    display-only caveat as the admin view.
    """
    puuids = {
        str(puuid)
        for (puuid,) in (
            await session.execute(select(RiotAccount.puuid).where(RiotAccount.user_id == user_id))
        ).all()
    }
    if not puuids:
        return []
    games = await list_match_history(session, limit=max(limit * 5, 100))
    mine = [g for g in games if any(p.puuid in puuids for team in g.teams for p in team.players)]
    return mine[:limit]


async def list_match_history(session: AsyncSession, *, limit: int = 50) -> list[HistoryGame]:
    """Reconstruct recent per-game scoreboards from persisted ``result_reports``.

    For display only (PLAN.md §3.6.5): each client posts the FULL eog scoreboard,
    so a single ``ResultReport`` row already holds every player's champion + KDA.
    We pick ONE representative report per ``(match_id, lcu_game_id)`` (the most
    recent received), read its ``raw_payload.teams``, and resolve participant
    PUUIDs to Discord/Riot names via the global ``RiotAccount`` join. This is the
    forgeable, uncorroborated view — the official winner still comes from the
    adjudicator / admin override, never this read.

    RLS-scoped: only the acting guild's reports are visible (the GUC is set by the
    tenant session). ``RiotAccount``/``User`` are global, so name resolution spans
    every linked player.
    """
    rows = (
        (await session.execute(select(ResultReport).order_by(ResultReport.received_at.desc())))
        .scalars()
        .all()
    )

    # Dedupe to one representative report per game, newest first, capped at limit.
    representative: dict[tuple[str, str], ResultReport] = {}
    order: list[tuple[str, str]] = []
    for r in rows:
        key = (str(r.match_id), r.lcu_game_id)
        if key in representative:
            continue
        if not isinstance(r.raw_payload, dict):
            continue
        teams_raw = r.raw_payload.get("teams")
        if not isinstance(teams_raw, list) or not teams_raw:
            continue
        representative[key] = r
        order.append(key)
        if len(order) >= limit:
            break
    if not order:
        return []

    # Batch-resolve PUUID -> (discord display name, riot id) across all games.
    puuids: set[str] = set()
    for key in order:
        for team in representative[key].raw_payload["teams"]:  # type: ignore[index]
            for p in team.get("players") or []:
                if isinstance(p, dict) and p.get("puuid"):
                    puuids.add(str(p["puuid"]))
    names: dict[str, tuple[str | None, str | None]] = {}
    if puuids:
        for puuid, dname, gname, tline in (
            await session.execute(
                select(
                    RiotAccount.puuid,
                    User.display_name,
                    RiotAccount.riot_id_game_name,
                    RiotAccount.riot_id_tag_line,
                )
                .join(User, RiotAccount.user_id == User.id)
                .where(RiotAccount.puuid.in_(puuids))
            )
        ).all():
            riot_id = f"{gname}#{tline}" if gname and tline else None
            names[str(puuid)] = (dname, riot_id)

    # Batch-resolve match -> (round, tournament id, tournament name).
    match_ids = {uuid.UUID(key[0]) for key in order}
    meta: dict[str, tuple[int | None, str, str | None]] = {}
    for mid, rnd, tid, tname in (
        await session.execute(
            select(Match.id, Match.round, Match.tournament_id, Tournament.name)
            .join(Tournament, Match.tournament_id == Tournament.id)
            .where(Match.id.in_(match_ids))
        )
    ).all():
        meta[str(mid)] = (rnd, str(tid), tname)

    games: list[HistoryGame] = []
    for key in order:
        r = representative[key]
        rnd, tid, tname = meta.get(key[0], (None, "", None))
        teams = [
            HistoryTeam(
                is_winner=bool(team.get("isWinningTeam")),
                players=_history_players(team, names),
            )
            for team in r.raw_payload["teams"]  # type: ignore[index]
            if isinstance(team, dict)
        ]
        games.append(
            HistoryGame(
                match_id=key[0],
                tournament_id=tid,
                tournament_name=tname,
                round=rnd,
                lcu_game_id=key[1],
                played_at=r.received_at,
                teams=teams,
            )
        )
    return games
