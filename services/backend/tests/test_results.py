"""Tests for match-result recording -> bracket advancement (PLAN.md §3.6.6 / §2).

Two layers, both offline (no network, no real DB):

* The **pure bracket geometry** (:func:`next_slot_for`, :func:`place_winner_in_slot`)
  is exhaustively asserted — the slot map (winner of round ``r`` slot ``s`` feeds
  round ``r + 1`` slot ``s // 2``; even -> ``team_a``, odd -> ``team_b``) and the
  conditional, idempotent fill (only writes an empty slot, flips ``PENDING`` when
  both sides are present).
* The **async service** runs against a small in-memory fake ``AsyncSession``
  (mirrors ``tests/test_accounts.py``) seeded with a real bracket from
  ``plan_bracket_matches``, driving single-elimination advancement across an
  8-team bracket and a 5-team bracket whose byes auto-advance.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from lol_schemas.enums import (
    MatchResultState,
    MatchStatus,
    ResultDecidedBy,
    TournamentStatus,
)

from app.models import Match, MatchResult, Tournament
from app.results import service
from app.results.service import (
    MatchNotFoundError,
    MatchNotReadyError,
    TeamNotInMatchError,
    next_slot_for,
    place_winner_in_slot,
    record_winner,
)
from app.tournaments.service import plan_bracket_matches


# ---------------------------------------------------------------------------
# In-memory fake AsyncSession (matches/match_results only)
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Dict-backed AsyncSession stand-in covering the results code paths.

    Supports the narrow surface the service touches: ``execute`` of a simple
    ``select(Model).where(col == val [AND ...])``, ``add``, and ``flush``.
    """

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {Match: [], MatchResult: [], Tournament: []}

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.store.setdefault(type(obj), []).append(obj)

    def add_all(self, objs: list[Any]) -> None:
        for o in objs:
            self.add(o)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def execute(self, statement: Any, params: Any = None) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        model = entity if isinstance(entity, type) else getattr(entity, "class_", entity)
        crit = _equality_filters(statement)
        rows = [r for r in self.store.get(model, []) if _matches(r, crit)]
        return _Result(rows)


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = getattr(right, "value", right)
    return crit


def _flatten_and(clause: Any) -> list[Any]:
    if clause is None:
        return []
    if clause.__class__.__name__ == "BooleanClauseList":
        out: list[Any] = []
        for c in clause.clauses:
            out.extend(_flatten_and(c))
        return out
    return [clause]


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    return all(str(getattr(row, k, None)) == str(v) for k, v in crit.items())


# ---------------------------------------------------------------------------
# Bracket fixtures
# ---------------------------------------------------------------------------
def _seed_bracket(n: int) -> tuple[FakeSession, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Build a fresh n-team bracket in a fake session; return (session, tid, gid, teams)."""
    tid, gid = uuid.uuid4(), uuid.uuid4()
    teams = [uuid.uuid4() for _ in range(n)]
    session = FakeSession()
    session.add_all(plan_bracket_matches(tournament_id=tid, guild_id=gid, seeded_team_ids=teams))
    # A started bracket belongs to a RUNNING tournament — the state the last
    # decided match is supposed to move off.
    session.store[Tournament].append(
        Tournament(id=tid, guild_id=gid, name="Cup", status=TournamentStatus.RUNNING)
    )
    return session, tid, gid, teams


def _match_at(session: FakeSession, round_no: int, slot: int) -> Match:
    for m in session.store[Match]:
        if m.round == round_no and m.bracket_slot == slot:
            return m
    raise AssertionError(f"no match at round={round_no} slot={slot}")


# ---------------------------------------------------------------------------
# Pure geometry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "round_no, slot, exp_round, exp_slot, exp_is_a",
    [
        (1, 0, 2, 0, True),
        (1, 1, 2, 0, False),
        (1, 2, 2, 1, True),
        (1, 3, 2, 1, False),
        (2, 0, 3, 0, True),
        (2, 1, 3, 0, False),
        (3, 6, 4, 3, True),
        (3, 7, 4, 3, False),
    ],
)
def test_next_slot_map(round_no, slot, exp_round, exp_slot, exp_is_a) -> None:
    ns = next_slot_for(round_no, slot)
    assert (ns.round, ns.slot, ns.is_team_a) == (exp_round, exp_slot, exp_is_a)


def test_place_winner_fills_empty_team_a_only() -> None:
    nxt = Match(
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=2,
        bracket_slot=0,
        team_a=None,
        team_b=None,
        status=MatchStatus.PENDING,
    )
    w = uuid.uuid4()
    assert place_winner_in_slot(nxt, w, is_team_a=True) is True
    assert nxt.team_a == w and nxt.team_b is None
    # Only one side present -> not yet a playable PENDING-with-both match.
    assert nxt.team_b is None


def test_place_winner_idempotent_no_overwrite() -> None:
    w1, w2 = uuid.uuid4(), uuid.uuid4()
    nxt = Match(
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=2,
        bracket_slot=0,
        team_a=w1,
        team_b=None,
        status=MatchStatus.PENDING,
    )
    # Replaying the same fill is a no-op; a racing different team never overwrites.
    assert place_winner_in_slot(nxt, w1, is_team_a=True) is False
    assert place_winner_in_slot(nxt, w2, is_team_a=True) is False
    assert nxt.team_a == w1


def test_place_winner_both_present_flips_pending() -> None:
    a = uuid.uuid4()
    nxt = Match(
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=2,
        bracket_slot=0,
        team_a=a,
        team_b=None,
        status=MatchStatus.SCHEDULED,
    )
    assert place_winner_in_slot(nxt, uuid.uuid4(), is_team_a=False) is True
    assert nxt.team_a is not None and nxt.team_b is not None
    assert nxt.status == MatchStatus.PENDING


# ---------------------------------------------------------------------------
# Async recording + advancement
# ---------------------------------------------------------------------------
async def test_record_winner_stamps_match_and_result() -> None:
    session, tid, gid, teams = _seed_bracket(8)
    r1s0 = _match_at(session, 1, 0)
    winner = r1s0.team_a

    result = await record_winner(session, match_id=r1s0.id, winner_team_id=winner)

    assert r1s0.winner_team_id == winner
    assert r1s0.status == MatchStatus.COMPLETED
    assert isinstance(result, MatchResult)
    assert result.match_id == r1s0.id
    assert result.tournament_id == tid
    assert result.guild_id == gid
    assert result.winner_team_id == winner
    assert result.decided_by == ResultDecidedBy.ADMIN_OVERRIDE
    assert result.state == MatchResultState.CONFIRMED
    assert result.decided_at is not None


async def test_winner_advances_into_next_round_slot() -> None:
    session, _, _, _ = _seed_bracket(8)
    r1s0, r1s1 = _match_at(session, 1, 0), _match_at(session, 1, 1)
    nxt = _match_at(session, 2, 0)

    w0 = r1s0.team_a
    await record_winner(session, match_id=r1s0.id, winner_team_id=w0)
    # Even source slot -> team_a; only one feeder decided so far.
    assert nxt.team_a == w0 and nxt.team_b is None

    w1 = r1s1.team_b
    await record_winner(session, match_id=r1s1.id, winner_team_id=w1)
    # Odd source slot -> team_b; both feeders present -> playable PENDING match.
    assert nxt.team_a == w0 and nxt.team_b == w1
    assert nxt.status == MatchStatus.PENDING


async def test_full_8team_advancement_to_champion() -> None:
    session, _, _, _ = _seed_bracket(8)
    # Round 1: top team of each match wins.
    for slot in range(4):
        m = _match_at(session, 1, slot)
        await record_winner(session, match_id=m.id, winner_team_id=m.team_a)
    # Round 2 (semis) now have both teams; top wins again.
    for slot in range(2):
        m = _match_at(session, 2, slot)
        assert m.team_a is not None and m.team_b is not None
        await record_winner(session, match_id=m.id, winner_team_id=m.team_a)
    # Final has both finalists; decide the champion.
    final = _match_at(session, 3, 0)
    assert final.team_a is not None and final.team_b is not None
    champ = final.team_a
    res = await record_winner(session, match_id=final.id, winner_team_id=champ)
    assert final.winner_team_id == champ
    assert final.status == MatchStatus.COMPLETED
    assert res.winner_team_id == champ
    # Final has no successor — nothing beyond round 3 exists.
    assert max(m.round for m in session.store[Match]) == 3


async def test_record_winner_idempotent_replay() -> None:
    session, _, _, _ = _seed_bracket(8)
    r1s0 = _match_at(session, 1, 0)
    nxt = _match_at(session, 2, 0)
    winner = r1s0.team_a

    r1 = await record_winner(session, match_id=r1s0.id, winner_team_id=winner)
    r2 = await record_winner(session, match_id=r1s0.id, winner_team_id=winner)

    # Exactly one MatchResult row (UNIQUE(match_id)); same row updated in place.
    assert len(session.store[MatchResult]) == 1
    assert r1.id == r2.id
    # The next slot was filled once, not duplicated.
    assert nxt.team_a == winner and nxt.team_b is None


async def test_bye_auto_advances_via_chain() -> None:
    # 5 teams -> 8-slot bracket -> 3 byes (top seeds). The bye winners advance one
    # hop into the semis without an admin call.
    session, _, _, teams = _seed_bracket(5)
    byes = [m for m in session.store[Match] if m.status == MatchStatus.WALKOVER]
    assert len(byes) == 3
    for bye in byes:
        assert bye.winner_team_id == bye.team_a
        await service.advance_bye_chain(session, bye)

    # Every bye's winner now occupies its successor's correct side.
    for bye in byes:
        target = next_slot_for(bye.round, bye.bracket_slot)
        nxt = _match_at(session, target.round, target.slot)
        side = nxt.team_a if target.is_team_a else nxt.team_b
        assert side == bye.winner_team_id


async def test_advance_bye_chain_cascades_two_hops() -> None:
    # Hand-built degenerate bracket: two round-1 byes both feed the same round-2
    # placeholder, which thus becomes a single-team pre-decided bye that must
    # itself cascade into round 3. Verifies the chain walks more than one hop.
    tid, gid = uuid.uuid4(), uuid.uuid4()
    a = uuid.uuid4()
    session = FakeSession()
    # round 1: only slot 0 is a real bye carrying team a; slot 1..3 absent feeders.
    r1 = Match(
        guild_id=gid,
        tournament_id=tid,
        round=1,
        bracket_slot=0,
        team_a=a,
        team_b=None,
        winner_team_id=a,
        status=MatchStatus.WALKOVER,
    )
    # round 2 slot 0 is a placeholder; round 3 slot 0 is the final placeholder.
    r2 = Match(
        guild_id=gid,
        tournament_id=tid,
        round=2,
        bracket_slot=0,
        team_a=None,
        team_b=None,
        status=MatchStatus.PENDING,
    )
    r3 = Match(
        guild_id=gid,
        tournament_id=tid,
        round=3,
        bracket_slot=0,
        team_a=None,
        team_b=None,
        status=MatchStatus.PENDING,
    )
    session.add_all([r1, r2, r3])

    # Pre-decide r2 as a bye (single team already winning) so the cascade continues.
    r2.team_a = a
    r2.winner_team_id = a
    r2.status = MatchStatus.WALKOVER

    await service.advance_bye_chain(session, r1)
    # r1 -> r2.team_a (already a); r2 is a decided bye -> cascades to r3.team_a.
    assert r2.team_a == a
    assert r3.team_a == a


async def test_record_winner_rejects_non_participant() -> None:
    session, _, _, _ = _seed_bracket(8)
    r1s0 = _match_at(session, 1, 0)
    with pytest.raises(TeamNotInMatchError):
        await record_winner(session, match_id=r1s0.id, winner_team_id=uuid.uuid4())


async def test_record_winner_rejects_unready_match() -> None:
    session, _, _, _ = _seed_bracket(8)
    semi = _match_at(session, 2, 0)  # placeholder, no teams yet
    assert semi.team_a is None
    with pytest.raises(MatchNotReadyError):
        await record_winner(session, match_id=semi.id, winner_team_id=uuid.uuid4())


async def test_record_winner_unknown_match() -> None:
    session, _, _, _ = _seed_bracket(8)
    with pytest.raises(MatchNotFoundError):
        await record_winner(session, match_id=uuid.uuid4(), winner_team_id=uuid.uuid4())


async def test_admin_override_audit_fields_recorded() -> None:
    session, _, _, _ = _seed_bracket(8)
    r1s0 = _match_at(session, 1, 0)
    admin = uuid.uuid4()
    res = await record_winner(
        session,
        match_id=r1s0.id,
        winner_team_id=r1s0.team_b,
        decided_by=ResultDecidedBy.ADMIN_OVERRIDE,
        admin_override_by=admin,
        admin_override_reason="disputed eog",
    )
    assert res.admin_override_by == admin
    assert res.admin_override_reason == "disputed eog"
    assert res.winner_team_id == r1s0.team_b


async def test_quorum_decision_clears_override_fields() -> None:
    session, _, _, _ = _seed_bracket(8)
    r1s0 = _match_at(session, 1, 0)
    res = await record_winner(
        session,
        match_id=r1s0.id,
        winner_team_id=r1s0.team_a,
        decided_by=ResultDecidedBy.QUORUM,
        admin_override_by=uuid.uuid4(),  # ignored for non-override decisions
        admin_override_reason="should be dropped",
    )
    assert res.decided_by == ResultDecidedBy.QUORUM
    assert res.admin_override_by is None
    assert res.admin_override_reason is None


# ---------------------------------------------------------------------------
# Match history (read model) — reconstruct per-game scoreboards from reports
# ---------------------------------------------------------------------------
class _HistResult:
    """A result that answers both ``.scalars().all()`` and ``.all()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _HistResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _QueuedSession:
    """Returns pre-baked results in execute() call order (reports, names, meta)."""

    def __init__(self, *results: _HistResult) -> None:
        self._q = list(results)

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return self._q.pop(0)


def _report(match_id: uuid.UUID, game_id: str, received_at: Any, teams: list[Any]) -> Any:
    from app.models import ResultReport

    return ResultReport(
        match_id=match_id,
        lcu_game_id=game_id,
        reporter_puuid="p1",
        raw_payload={"teams": teams},
        received_at=received_at,
    )


@pytest.mark.asyncio
async def test_list_match_history_dedupes_and_resolves() -> None:
    from datetime import UTC, datetime

    match_id = uuid.uuid4()
    tid = uuid.uuid4()
    teams = [
        {
            "isWinningTeam": True,
            "players": [
                {"puuid": "p1", "championId": 1, "championsKilled": 7, "numDeaths": 2, "assists": 9}
            ],
        },
        {
            "isWinningTeam": False,
            "players": [
                {"puuid": "p2", "championId": 5, "championsKilled": 1, "numDeaths": 8, "assists": 3}
            ],
        },
    ]
    newest = _report(match_id, "100", datetime(2026, 6, 3, tzinfo=UTC), teams)
    older_dup = _report(match_id, "100", datetime(2026, 6, 1, tzinfo=UTC), teams)
    other_game = _report(match_id, "200", datetime(2026, 6, 2, tzinfo=UTC), teams)

    session = _QueuedSession(
        _HistResult([newest, other_game, older_dup]),  # reports, newest-first
        _HistResult([("p1", "Chama", "ChamaIGN", "EUNE")]),  # name resolution (p2 unlinked)
        _HistResult([(match_id, 2, tid, "Summer Cup")]),  # match -> tournament meta
    )

    games = await service.list_match_history(session, limit=50)  # type: ignore[arg-type]

    # Two distinct games (the older duplicate of game 100 is dropped).
    assert [g.lcu_game_id for g in games] == ["100", "200"]
    g = games[0]
    assert g.match_id == str(match_id)
    assert g.tournament_name == "Summer Cup"
    assert g.round == 2
    assert g.teams[0].is_winner is True
    win = g.teams[0].players[0]
    assert win.champion_id == 1
    assert (win.kills, win.deaths, win.assists) == (7, 2, 9)
    assert win.display_name == "Chama"
    assert win.riot_id == "ChamaIGN#EUNE"
    # Unlinked player resolves to no name.
    assert g.teams[1].players[0].display_name is None
    assert g.teams[1].players[0].riot_id is None


@pytest.mark.asyncio
async def test_list_match_history_empty_when_no_reports() -> None:
    session = _QueuedSession(_HistResult([]))
    assert await service.list_match_history(session, limit=50) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Disputes — freeze a match as DISPUTED (no winner, no advance)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mark_disputed_creates_disputed_result_no_winner() -> None:
    from lol_schemas.enums import MatchResultState

    from app.models import MatchResult

    session, _tid, _gid, _teams = _seed_bracket(4)
    match = _match_at(session, 1, 0)

    result = await service.mark_disputed(
        session,
        match_id=match.id,
        agreed_game_id="100",
        anomaly_flags=["asymmetric_winner_claim"],
        reports_counted=10,
        both_teams_represented=True,
    )

    assert result is not None
    assert result.state == MatchResultState.DISPUTED
    assert result.winner_team_id is None
    assert result.quorum_detail["anomaly_flags"] == ["asymmetric_winner_claim"]
    # The match is NOT stamped completed — it waits for an admin override.
    assert match.status != MatchStatus.COMPLETED
    assert session.store[MatchResult][0].state == MatchResultState.DISPUTED


@pytest.mark.asyncio
async def test_admin_override_resolves_a_queued_dispute() -> None:
    """Resolving is the dispute queue's whole point — the row upgrades in place.

    ``mark_disputed`` writes ``winner_team_id=None``, so the old conflict guard
    409'd every admin resolution and the queue was a write-only inbox.
    """
    session, _tid, _gid, _teams = _seed_bracket(4)
    match = _match_at(session, 1, 0)
    await service.mark_disputed(
        session,
        match_id=match.id,
        agreed_game_id="100",
        anomaly_flags=["asymmetric_winner_claim"],
        reports_counted=10,
        both_teams_represented=True,
    )

    admin = uuid.uuid4()
    result = await record_winner(
        session,
        match_id=match.id,
        winner_team_id=match.team_a,
        decided_by=ResultDecidedBy.ADMIN_OVERRIDE,
        admin_override_by=admin,
        admin_override_reason="reviewed the scoreboards",
    )

    assert result.state == MatchResultState.CONFIRMED
    assert result.winner_team_id == match.team_a
    assert result.decided_by == ResultDecidedBy.ADMIN_OVERRIDE
    assert result.admin_override_by == admin
    # Upgraded IN PLACE — never a second MatchResult row for the same match.
    assert len(session.store[MatchResult]) == 1
    assert match.status == MatchStatus.COMPLETED
    assert match.winner_team_id == match.team_a


@pytest.mark.asyncio
async def test_late_quorum_cannot_resolve_a_queued_dispute() -> None:
    """A dispute froze the match BECAUSE reports conflicted — only an admin may
    unfreeze it; a late quorum re-run must not paper over the conflict."""
    session, _tid, _gid, _teams = _seed_bracket(4)
    match = _match_at(session, 1, 0)
    await service.mark_disputed(
        session,
        match_id=match.id,
        agreed_game_id="100",
        anomaly_flags=["asymmetric_winner_claim"],
        reports_counted=10,
        both_teams_represented=True,
    )

    with pytest.raises(service.ResultConflictError):
        await record_winner(
            session,
            match_id=match.id,
            winner_team_id=match.team_a,
            decided_by=ResultDecidedBy.QUORUM,
        )


async def test_the_final_closes_the_tournament() -> None:
    """A crowned champion should not leave the tournament reading as live.

    Advancement stops at the final by design (there is no successor slot), so
    nothing used to move the tournament off RUNNING — the bracket was fully
    decided and the status still said the event was in progress.
    """
    session, tid, _, _ = _seed_bracket(2)
    final = _match_at(session, 1, 0)
    tournament = session.store[Tournament][0]

    await record_winner(session, match_id=final.id, winner_team_id=final.team_a)

    assert tournament.status is TournamentStatus.COMPLETED


async def test_a_tournament_with_matches_left_stays_running() -> None:
    session, _, _, _ = _seed_bracket(4)
    tournament = session.store[Tournament][0]
    first = _match_at(session, 1, 0)

    await record_winner(session, match_id=first.id, winner_team_id=first.team_a)

    assert tournament.status is TournamentStatus.RUNNING, "the other half has not played"


async def test_replaying_the_final_repairs_a_tournament_left_running() -> None:
    """Re-recording the same winner is an admin's only no-op — make it heal.

    A bracket decided by an earlier build sits at RUNNING forever with no
    command to fix it: the winner cannot be changed, and there is nothing left
    to play. Replaying the decided result closes it.
    """
    session, _, _, _ = _seed_bracket(2)
    final = _match_at(session, 1, 0)
    tournament = session.store[Tournament][0]

    await record_winner(session, match_id=final.id, winner_team_id=final.team_a)
    tournament.status = TournamentStatus.RUNNING  # as an older build left it

    await record_winner(session, match_id=final.id, winner_team_id=final.team_a)

    assert tournament.status is TournamentStatus.COMPLETED
