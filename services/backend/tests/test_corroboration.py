"""Tests for automatic result corroboration (reports -> decided winner).

Offline: the pure helpers (winning-PUUID extraction, winner-team mapping) plus the
``adjudicate_match`` wiring driven against a small queued fake session and a stub
``record_winner`` seam — so we assert the ACCEPT/DISPUTE/INSUFFICIENT branches and
that a clean >=6/10 quorum auto-records the winner, without running the full
bracket-advance path (that is covered in test_results.py).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from lol_core.adjudication import AdjudicationOutcome
from lol_schemas.enums import MatchStatus, ResultDecidedBy, ResultSource

from app.results import corroboration


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _payload(winner_puuids: list[str], loser_puuids: list[str]) -> dict[str, Any]:
    return {
        "teams": [
            {"isWinningTeam": True, "players": [{"puuid": p} for p in winner_puuids]},
            {"isWinningTeam": False, "players": [{"puuid": p} for p in loser_puuids]},
        ]
    }


def test_winning_puuids_extracts_only_the_flagged_team() -> None:
    pay = _payload(["a1", "a2"], ["b1", "b2"])
    assert corroboration._winning_puuids(pay) == {"a1", "a2"}


def test_winning_puuids_tolerates_malformed() -> None:
    assert corroboration._winning_puuids(None) == set()
    assert corroboration._winning_puuids({"teams": "nope"}) == set()
    assert corroboration._winning_puuids({}) == set()


def test_derive_winner_team_maps_by_roster_overlap() -> None:
    ta, tb = uuid.uuid4(), uuid.uuid4()
    roster = {ta: frozenset({"a1", "a2", "a3"}), tb: frozenset({"b1", "b2", "b3"})}
    assert corroboration._derive_winner_team({"a1", "a2"}, roster) == ta
    assert corroboration._derive_winner_team({"b1", "b2", "b3"}, roster) == tb


def test_derive_winner_team_none_on_tie_or_no_overlap() -> None:
    ta, tb = uuid.uuid4(), uuid.uuid4()
    roster = {ta: frozenset({"a1"}), tb: frozenset({"b1"})}
    assert corroboration._derive_winner_team({"a1", "b1"}, roster) is None  # tie
    assert corroboration._derive_winner_team({"zz"}, roster) is None  # no overlap


# ---------------------------------------------------------------------------
# adjudicate_match wiring
# ---------------------------------------------------------------------------
class _Res:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _Res:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _QueuedSession:
    """Returns canned results in execute() call order: Match, TM(a), TM(b), CS, RR."""

    def __init__(self, *results: _Res) -> None:
        self._q = list(results)

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return self._q.pop(0)


class _RecordSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def _report(reporter: str, winner_puuids: list[str], loser_puuids: list[str]) -> Any:
    return SimpleNamespace(
        lcu_game_id="100",
        reporter_puuid=reporter,
        source=ResultSource.EOG_STATS_BLOCK,
        schema_version="v1",
        received_at=None,
        raw_payload=_payload(winner_puuids, loser_puuids),
    )


def _build(*, reports: list[Any]) -> tuple[_QueuedSession, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a queued session for a 5v5 match where A=team_a, B=team_b checked in."""
    match_id, ta, tb = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a_users = [uuid.uuid4() for _ in range(5)]
    b_users = [uuid.uuid4() for _ in range(5)]
    a_puuids = [f"pa{i}" for i in range(5)]
    b_puuids = [f"pb{i}" for i in range(5)]
    match = SimpleNamespace(id=match_id, status=MatchStatus.PENDING, team_a=ta, team_b=tb)
    sessions = [(a_users[i], a_puuids[i]) for i in range(5)] + [
        (b_users[i], b_puuids[i]) for i in range(5)
    ]
    session = _QueuedSession(
        _Res([match]),  # 1: select(Match)
        _Res(a_users),  # 2: TeamMember(team_a).user_id
        _Res(b_users),  # 3: TeamMember(team_b).user_id
        _Res(sessions),  # 4: ClientSession (user_id, puuid)
        _Res(reports),  # 5: ResultReport rows
    )
    return session, match_id, ta, tb


# Convenience: the 10 PUUIDs the 5v5 roster check-in produced.
_A = [f"pa{i}" for i in range(5)]
_B = [f"pb{i}" for i in range(5)]
_ALL = _A + _B


@pytest.mark.asyncio
async def test_quorum_agreement_auto_records_winner() -> None:
    # All 10 honest clients report team A won -> ACCEPT -> record_winner(team_a).
    reports = [_report(p, _A, _B) for p in _ALL]
    session, match_id, ta, _tb = _build(reports=reports)
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.ACCEPT
    assert len(spy.calls) == 1
    assert spy.calls[0]["winner_team_id"] == ta
    assert spy.calls[0]["decided_by"] == ResultDecidedBy.QUORUM


@pytest.mark.asyncio
async def test_too_few_reports_is_insufficient_no_record() -> None:
    reports = [_report(p, _A, _B) for p in _A[:3]]  # only 3 of 10
    session, match_id, _ta, _tb = _build(reports=reports)
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.INSUFFICIENT
    assert spy.calls == []


@pytest.mark.asyncio
async def test_conflicting_reports_dispute_no_record() -> None:
    # Half claim A won, half claim B won -> asymmetric winner claim -> DISPUTE.
    reports = [_report(p, _A, _B) for p in _A] + [_report(p, _B, _A) for p in _B]
    session, match_id, _ta, _tb = _build(reports=reports)
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.DISPUTE
    assert spy.calls == []


@pytest.mark.asyncio
async def test_already_completed_match_is_skipped() -> None:
    match = SimpleNamespace(
        id=uuid.uuid4(), status=MatchStatus.COMPLETED, team_a=uuid.uuid4(), team_b=uuid.uuid4()
    )
    session = _QueuedSession(_Res([match]))
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match.id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome is None
    assert spy.calls == []
