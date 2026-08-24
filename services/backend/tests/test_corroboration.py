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
from lol_schemas.enums import GameMode, MatchStatus, ResultDecidedBy, ResultSource

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

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _Res:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _QueuedSession:
    """Returns canned results in execute() call order: Match, TM(a), TM(b), CS, RR."""

    def __init__(self, *results: _Res) -> None:
        self._q = list(results)
        self.added: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return self._q.pop(0)

    def add(self, obj: Any) -> None:  # for the audit-trail write (no execute)
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _RecordSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def _report(
    reporter: str,
    winner_puuids: list[str],
    loser_puuids: list[str],
    *,
    game: str = "100",
    kill_stream: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
        lcu_game_id=game,
        reporter_puuid=reporter,
        source=ResultSource.EOG_STATS_BLOCK,
        schema_version="v1",
        received_at=None,
        raw_payload=_payload(winner_puuids, loser_puuids),
        reported_kda=None,
        kill_stream=kill_stream,
    )


def _build(
    *,
    reports: list[Any],
    game_mode: GameMode = GameMode.SUMMONERS_RIFT_5V5,
    players_per_team: int = 5,
    best_of: int = 1,
    sibling_scored: list[list[str]] | None = None,
) -> tuple[_QueuedSession, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a queued session for a match where A=team_a, B=team_b checked in.

    Mirrors adjudicate_match's execute() order: Match, TeamMember(a), TeamMember(b),
    ClientSession, (Tournament.game_mode, best_of), [RiotAccount names — only for a
    first-blood mode], ResultReport, sibling scored_game_ids.
    """
    n = players_per_team
    match_id, ta, tb = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a_users = [uuid.uuid4() for _ in range(n)]
    b_users = [uuid.uuid4() for _ in range(n)]
    a_puuids = [f"pa{i}" for i in range(n)]
    b_puuids = [f"pb{i}" for i in range(n)]
    match = SimpleNamespace(
        id=match_id,
        status=MatchStatus.PENDING,
        team_a=ta,
        team_b=tb,
        tournament_id=uuid.uuid4(),
        wins_a=0,
        wins_b=0,
        scored_game_ids=[],
    )
    sessions = [(a_users[i], a_puuids[i]) for i in range(n)] + [
        (b_users[i], b_puuids[i]) for i in range(n)
    ]
    results = [
        _Res([match]),  # 1: select(Match)
        _Res(a_users),  # 2: TeamMember(team_a).user_id
        _Res(b_users),  # 3: TeamMember(team_b).user_id
        _Res(sessions),  # 4: ClientSession (user_id, puuid)
        _Res([(game_mode, best_of)]),  # 5: (Tournament.game_mode, best_of)
    ]
    if game_mode.decides_on_first_blood:
        # 5b: RiotAccount (puuid, game_name, tag_line) for the kill-stream name map.
        names = [(p, p.upper(), "TAG") for p in a_puuids + b_puuids]
        results.append(_Res(names))
    results.extend(
        [
            _Res(reports),  # 6: ResultReport rows
            _Res(list(sibling_scored or [])),  # 7: sibling matches' scored_game_ids
        ]
    )
    session = _QueuedSession(*results)
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
    dispute_spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
        mark_disputed=dispute_spy,
    )

    assert outcome == AdjudicationOutcome.DISPUTE
    assert spy.calls == []  # no winner recorded
    assert len(dispute_spy.calls) == 1  # match frozen as disputed for the admin queue


@pytest.mark.asyncio
async def test_1v1_duel_both_agree_auto_records_with_2_of_2_quorum() -> None:
    # A 1v1 has only 2 reports, so the 6/10 default can never pass; the ARAM_1V1
    # mode threshold (2/2) must let both-agree resolve. Both players report A won.
    reports = [_report("pa0", ["pa0"], ["pb0"]), _report("pb0", ["pa0"], ["pb0"])]
    session, match_id, ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
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


@pytest.mark.asyncio
async def test_1v1_single_report_is_insufficient() -> None:
    # Only one of the two duelists has reported -> wait for the other (2/2 not met).
    reports = [_report("pa0", ["pa0"], ["pb0"])]
    session, match_id, _ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.INSUFFICIENT
    assert spy.calls == []


# ---------------------------------------------------------------------------
# First-blood decided modes (GameMode.decides_on_first_blood — the 1v1 duel)
# ---------------------------------------------------------------------------
def _fb_stream(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"source": "liveClientData", "events": events, "schemaVersion": "v1"}


def _fb_kill(scorer: str, victim: str, *, at: float = 123.4) -> dict[str, Any]:
    """A capture whose FirstBlood rides the usual same-tick ChampionKill pair."""
    return _fb_stream(
        [
            {"eventId": 1, "eventName": "GameStart", "eventTime": 0.0},
            {
                "eventId": 2,
                "eventName": "ChampionKill",
                "eventTime": at,
                "killerName": scorer,
                "victimName": victim,
            },
            {"eventId": 3, "eventName": "FirstBlood", "eventTime": at},
        ]
    )


def test_kill_stream_events_tolerates_malformed() -> None:
    assert corroboration._kill_stream_events(None) == []
    assert corroboration._kill_stream_events({"events": "nope"}) == []
    assert corroboration._kill_stream_events({"events": [1, {"eventName": "x"}]}) == [
        {"eventName": "x"}
    ]


@pytest.mark.asyncio
async def test_1v1_first_blood_overrides_eog_winner() -> None:
    # The duel is decided at first blood: both captures name PB0 as the scorer,
    # even though the eog block says team A won (e.g. A outlasted B after both
    # duelists stopped playing). The kill stream must out-vote the eog bit.
    stream = _fb_kill("PB0", "PA0")
    reports = [
        _report("pa0", ["pa0"], ["pb0"], kill_stream=stream),
        _report("pb0", ["pa0"], ["pb0"], kill_stream=stream),
    ]
    session, match_id, _ta, tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.ACCEPT
    assert len(spy.calls) == 1
    assert spy.calls[0]["winner_team_id"] == tb


@pytest.mark.asyncio
async def test_1v1_first_blood_recipient_decides() -> None:
    # A newer capture keeps the FirstBlood `Recipient` — it alone names the scorer.
    stream = _fb_stream(
        [{"eventId": 2, "eventName": "FirstBlood", "eventTime": 50.0, "recipient": "PB0#TAG"}]
    )
    reports = [
        _report("pa0", ["pa0"], ["pb0"], kill_stream=stream),
        _report("pb0", ["pa0"], ["pb0"], kill_stream=stream),
    ]
    session, match_id, _ta, tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.ACCEPT
    assert spy.calls[0]["winner_team_id"] == tb


@pytest.mark.asyncio
async def test_1v1_no_first_blood_falls_back_to_eog() -> None:
    # No FirstBlood in either capture (or no capture at all) -> the eog winner bit
    # still decides, so a normally-finished duel resolves exactly as before.
    stream = _fb_stream([{"eventId": 1, "eventName": "GameStart", "eventTime": 0.0}])
    reports = [
        _report("pa0", ["pa0"], ["pb0"], kill_stream=stream),
        _report("pb0", ["pa0"], ["pb0"], kill_stream=None),
    ]
    session, match_id, ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
    spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
    )

    assert outcome == AdjudicationOutcome.ACCEPT
    assert spy.calls[0]["winner_team_id"] == ta


@pytest.mark.asyncio
async def test_1v1_split_first_blood_claims_dispute() -> None:
    # Each duelist's capture names THEMSELVES as the scorer -> contradicting votes
    # with both reports in -> freeze for the admin queue, never auto-pick.
    reports = [
        _report("pa0", ["pa0"], ["pb0"], kill_stream=_fb_kill("PA0", "PB0")),
        _report("pb0", ["pa0"], ["pb0"], kill_stream=_fb_kill("PB0", "PA0")),
    ]
    session, match_id, _ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1
    )
    spy = _RecordSpy()
    dispute_spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
        mark_disputed=dispute_spy,
    )

    assert outcome == AdjudicationOutcome.DISPUTE
    assert spy.calls == []


@pytest.mark.asyncio
async def test_best_of_three_decides_after_two_game_wins() -> None:
    # Two distinct games (100, 200), both won by A -> series 2-0 -> ACCEPT team_a.
    reports = [
        _report("pa0", ["pa0"], ["pb0"], game="100"),
        _report("pb0", ["pa0"], ["pb0"], game="100"),
        _report("pa0", ["pa0"], ["pb0"], game="200"),
        _report("pb0", ["pa0"], ["pb0"], game="200"),
    ]
    session, match_id, ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1, best_of=3
    )
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


@pytest.mark.asyncio
async def test_best_of_three_one_game_in_is_insufficient() -> None:
    # Only game 1 decided (A) -> series 1-0, not yet at 2 wins -> no advance.
    reports = [
        _report("pa0", ["pa0"], ["pb0"], game="100"),
        _report("pb0", ["pa0"], ["pb0"], game="100"),
    ]
    session, match_id, _ta, _tb = _build(
        reports=reports, game_mode=GameMode.ARAM_1V1, players_per_team=1, best_of=3
    )
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


@pytest.mark.asyncio
async def test_game_id_already_scored_by_a_sibling_match_disputes() -> None:
    """A full agreeing quorum whose eog block was already consumed by ANOTHER
    match in the tournament must trip the reuse guard, never score — replaying
    one real game's payload into a second bracket slot is a forgery vector."""
    reports = [_report(p, _A, _B, game="100") for p in _A + _B]
    session, match_id, _ta, _tb = _build(reports=reports, sibling_scored=[["100"]])
    spy = _RecordSpy()
    dispute_spy = _RecordSpy()

    outcome = await corroboration.adjudicate_match(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        guild_id=uuid.uuid4(),
        record_winner=spy,
        mark_disputed=dispute_spy,
    )

    assert outcome == AdjudicationOutcome.DISPUTE
    assert spy.calls == []
    assert len(dispute_spy.calls) == 1
    assert "reused_game_id" in dispute_spy.calls[0]["anomaly_flags"]
