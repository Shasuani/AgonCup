"""Adjudication core: contract + adversarial corpus (PLAN.md §3.6.6).

Offline by design — pure-function assertions, no network, no DB. The corpus
constructs the (up to 10) forgeable per-participant reports for a single match and
asserts the ACCEPT / DISPUTE / INSUFFICIENT verdict + the audit trail, exercising
the quorum rule and every anomaly signal.
"""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from lol_schemas import PlayerKDA, ResultReportDTO
from lol_schemas.enums import ResultSource

from lol_core import (
    ANOMALY_FLAGS,
    QUORUM_THRESHOLD,
    TOTAL_PARTICIPANTS,
    AdjudicationOutcome,
    AdjudicationResult,
    KillStreamSummary,
    adjudicate_result,
    derive_first_blood_team,
)
from lol_core.adjudication import (
    FLAG_ASYMMETRIC_WINNER_CLAIM,
    FLAG_BYTE_IDENTICAL_PAYLOAD,
    FLAG_DUPLICATE_CONFLICTING_REPORT,
    FLAG_GAME_ID_SPLIT,
    FLAG_KDA_INTERNAL_INCONSISTENCY,
    FLAG_KILLSTREAM_CONTRADICTION,
    FLAG_MINOR_WINNER_DISSENT,
    FLAG_MISSING_REPORTS,
    FLAG_ONE_SIDED_QUORUM,
    FLAG_OUTSIDER_REPORT,
    FLAG_OVERSUBSCRIBED_REPORTS,
    FLAG_REUSED_GAME_ID,
    FLAG_ROSTER_MISCONFIGURED,
    FLAG_ROSTER_OVERLAP,
    FLAG_SCHEMA_VERSION_DISAGREEMENT,
    FLAG_SCHEMA_VERSION_MINORITY,
    FLAG_WINNER_NOT_IN_MATCH,
)

# --------------------------------------------------------------------------- #
# Fixtures: two 5-player rosters + a report builder                            #
# --------------------------------------------------------------------------- #
TEAM_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TEAM_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TEAM_C = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")  # never in a roster

A = [f"a{i}" for i in range(1, 7)]  # a1..a6 (6 so one-sided cases have a >=6 roster)
B = [f"b{i}" for i in range(1, 6)]  # b1..b5

ROSTER = {TEAM_A: frozenset(A[:5]), TEAM_B: frozenset(B)}
GUILD = uuid4()
MATCH = uuid4()


def rep(
    puuid: str,
    winner: UUID | None = None,
    *,
    game: str = "G1",
    source: ResultSource = ResultSource.EOG_STATS_BLOCK,
    kda: list[PlayerKDA] | None = None,
    nonce: str | None = None,
    schema_version: str | None = None,
    received_at: datetime | None = None,
    rid: UUID | None = None,
) -> ResultReportDTO:
    return ResultReportDTO(
        id=rid,
        guild_id=GUILD,
        match_id=MATCH,
        lcu_game_id=game,
        reporter_puuid=puuid,
        source=source,
        winner=winner,
        per_player_kda=kda or [],
        nonce=nonce,
        schema_version=schema_version,
        received_at=received_at,
    )


def both_teams_for(puuids: list[str], winner: UUID) -> list[ResultReportDTO]:
    return [rep(p, winner) for p in puuids]


# --------------------------------------------------------------------------- #
# Contract / smoke (carried over from the Phase-0 stub tests)                  #
# --------------------------------------------------------------------------- #
def test_quorum_constants() -> None:
    assert QUORUM_THRESHOLD == 6
    assert TOTAL_PARTICIPANTS == 10


def test_adjudication_outcomes_present() -> None:
    assert {o.value for o in AdjudicationOutcome} == {"accept", "dispute", "insufficient"}


def test_empty_reports_is_insufficient_not_raise() -> None:
    # Replaces the Phase-0 stub test: an empty cohort no longer raises; it is a
    # benign, reachable shortfall.
    result = adjudicate_result([], ROSTER)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.reports_counted == 0
    assert result.winner_team_id is None


def test_lol_schemas_importable_from_core_context() -> None:
    from lol_schemas import LobbyPhase, MatchResultDTO, ResultReportDTO, TournamentDTO

    assert LobbyPhase.IN_GAME.value == "in_game"
    t = TournamentDTO(id=uuid4(), guild_id=uuid4(), name="Smoke Cup")
    assert t.max_teams == 16
    assert ResultReportDTO is not None
    assert MatchResultDTO is not None


# --------------------------------------------------------------------------- #
# Happy path / quorum                                                          #
# --------------------------------------------------------------------------- #
def test_happy_6_three_plus_three_both_represented() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "b1", "b2", "b3"], TEAM_A)
    reports += [rep(p) for p in ("a4", "a5", "b4", "b5")]  # 4 None-winner
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.winner_team_id == TEAM_A
    assert result.both_teams_represented is True
    assert result.reports_counted == 6
    assert result.anomaly_flags == []  # clean accept → no audit flags


def test_happy_10_unanimous() -> None:
    reports = both_teams_for(A[:5] + B, TEAM_A)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.reports_counted == 10
    assert result.anomaly_flags == []


def test_exact_quorum_single_cross_roster_reporter() -> None:
    reports = both_teams_for(["b1", "b2", "b3", "b4", "b5", "a1"], TEAM_B)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.winner_team_id == TEAM_B
    assert result.both_teams_represented is True


def test_missing_reports_audit_flag_does_not_block_accept() -> None:
    # 8 of 10 reported (both teams, quorum met) → ACCEPT, but the audit flag notes
    # the incomplete cohort. The LOW signal must never change the outcome.
    reports = both_teams_for(["a1", "a2", "a3", "a4", "a5", "b1", "b2", "b3"], TEAM_A)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert FLAG_MISSING_REPORTS in result.anomaly_flags


def test_single_dissenter_tolerated() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3"], TEAM_A)
    reports += [rep("b4", TEAM_B), rep("a5"), rep("b5")]
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.winner_team_id == TEAM_A
    assert FLAG_MINOR_WINNER_DISSENT in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Capture-race / reachability (benign → INSUFFICIENT)                          #
# --------------------------------------------------------------------------- #
def test_capture_race_four_reports() -> None:
    reports = both_teams_for(["a1", "a2", "b1", "b2"], TEAM_A)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.winner_team_id is None


def test_one_sided_below_threshold_is_insufficient() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "a5"], TEAM_A)  # team_b silent
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT


def test_all_null_winner_is_insufficient() -> None:
    reports = [rep(p) for p in ("a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4")]
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT


def test_all_reports_in_stalemate_is_dispute() -> None:
    # 5 agree team_a (both teams) but the other 5 reported None → all 10 in, no
    # quorum reachable → escalate rather than wait forever.
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1"], TEAM_A)
    reports += [rep(p) for p in ("a5", "b2", "b3", "b4", "b5")]
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE


# --------------------------------------------------------------------------- #
# One-sided quorum (roster with subs)                                         #
# --------------------------------------------------------------------------- #
def test_one_sided_quorum_reachable_is_insufficient() -> None:
    roster = {TEAM_A: frozenset(A), TEAM_B: frozenset(B)}  # team_a has 6 (a sub)
    reports = both_teams_for(A, TEAM_A)  # 6 from team_a only; team_b silent
    result = adjudicate_result(reports, roster)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert FLAG_ONE_SIDED_QUORUM in result.anomaly_flags


def test_one_sided_quorum_all_in_is_dispute() -> None:
    roster = {TEAM_A: frozenset(A), TEAM_B: frozenset(B)}
    reports = both_teams_for(A, TEAM_A)  # 6 team_a → team_a
    reports += [rep(p) for p in ("b1", "b2", "b3", "b4")]  # team_b reports None
    result = adjudicate_result(reports, roster)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_ONE_SIDED_QUORUM in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: contradictory / forged winners                                 #
# --------------------------------------------------------------------------- #
def test_asymmetric_winner_claim_both_sides() -> None:
    reports = both_teams_for(A[:5], TEAM_A) + both_teams_for(B, TEAM_B)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_ASYMMETRIC_WINNER_CLAIM in result.anomaly_flags


def test_winner_not_in_match() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4"], TEAM_C)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_WINNER_NOT_IN_MATCH in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: game-id binding                                                 #
# --------------------------------------------------------------------------- #
def test_split_game_id() -> None:
    reports = [rep(p, TEAM_A, game="G1") for p in ("a1", "a2", "a3", "b1")]
    reports += [rep(p, TEAM_A, game="G2") for p in ("a4", "a5", "b2")]
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_GAME_ID_SPLIT in result.anomaly_flags


def test_reused_game_id() -> None:
    reports = both_teams_for(A[:5] + B, TEAM_A)
    result = adjudicate_result(reports, ROSTER, used_game_ids=frozenset({"G1"}))
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_REUSED_GAME_ID in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: identity dedupe / source-inflation                             #
# --------------------------------------------------------------------------- #
def test_duplicate_resubmit_same_winner_accepts() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3"], TEAM_A)
    # a1 resubmits the same (game, puuid, source) with a later timestamp.
    reports.append(rep("a1", TEAM_A, received_at=datetime(2030, 1, 1, tzinfo=UTC)))
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.reports_counted == 7  # a1 counted once
    assert FLAG_DUPLICATE_CONFLICTING_REPORT not in result.anomaly_flags


def test_duplicate_conflicting_winner_disputes() -> None:
    reports = both_teams_for(["a2", "a3", "a4", "b1", "b2", "b3"], TEAM_A)
    reports += [rep("a1", TEAM_A), rep("a1", TEAM_B)]  # same key, conflicting winner
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_DUPLICATE_CONFLICTING_REPORT in result.anomaly_flags


def test_cross_source_dedupe_accepts_distinct_identities() -> None:
    reports: list[ResultReportDTO] = []
    for p in A[:5]:  # each team_a player reports via BOTH sources
        reports.append(rep(p, TEAM_A, source=ResultSource.EOG_STATS_BLOCK))
        reports.append(rep(p, TEAM_A, source=ResultSource.LIVE_CLIENT_DATA))
    reports.append(rep("b1", TEAM_A))  # one cross-roster reporter
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.reports_counted == 6  # 5 (collapsed) + 1, not 11


def test_cross_source_inflation_cannot_fake_quorum() -> None:
    reports: list[ResultReportDTO] = []
    for p in A[:5]:
        reports.append(rep(p, TEAM_A, source=ResultSource.EOG_STATS_BLOCK))
        reports.append(rep(p, TEAM_A, source=ResultSource.LIVE_CLIENT_DATA))
    result = adjudicate_result(reports, ROSTER)  # no team_b reporter at all
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.reports_counted == 5  # one team, collapsed


def test_oversubscribed_reports() -> None:
    reports = both_teams_for(A[:5] + B, TEAM_A)  # 10
    reports.append(rep("x1", TEAM_A))  # an 11th distinct identity
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_OVERSUBSCRIBED_REPORTS in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: outsiders                                                       #
# --------------------------------------------------------------------------- #
def test_outsider_masking_shortfall_is_insufficient() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "b1", "b2"], TEAM_A)  # 5 honest
    reports.append(rep("x1", TEAM_A))  # off-roster filler → excluded
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.reports_counted == 5
    assert FLAG_OUTSIDER_REPORT in result.anomaly_flags


def test_outsider_filler_with_real_quorum_accepts() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4"], TEAM_A)
    reports.append(rep("x1", TEAM_A))
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert FLAG_OUTSIDER_REPORT in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: byte-identical payloads (load-bearing escalation)              #
# --------------------------------------------------------------------------- #
_CLONE = [PlayerKDA(puuid="a1", kills=10, deaths=2, assists=5)]


def test_byte_identical_load_bearing_disputes() -> None:
    # 4 cloned reports ARE the quorum; without them only 2 honest remain.
    reports = [rep(p, TEAM_A, kda=_CLONE) for p in ("a1", "a2", "a3", "b1")]
    reports += [rep("b2", TEAM_A), rep("b3", TEAM_A)]
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_BYTE_IDENTICAL_PAYLOAD in result.anomaly_flags


def test_byte_identical_not_load_bearing_accepts() -> None:
    # 2 share the true scoreboard but quorum of 6 survives without them.
    reports = [rep("a1", TEAM_A, kda=_CLONE), rep("a2", TEAM_A, kda=_CLONE)]
    reports += both_teams_for(["a3", "a4", "b1", "b2", "b3", "b4"], TEAM_A)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert FLAG_BYTE_IDENTICAL_PAYLOAD in result.anomaly_flags


def test_empty_kda_never_byte_identical() -> None:
    reports = both_teams_for(A[:5] + B, TEAM_A)  # all empty KDA
    result = adjudicate_result(reports, ROSTER)
    assert FLAG_BYTE_IDENTICAL_PAYLOAD not in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: structurally impossible KDA                                     #
# --------------------------------------------------------------------------- #
def test_kda_duplicate_player_disputes() -> None:
    bad = [PlayerKDA(puuid="dup", kills=1, deaths=1, assists=1)] * 2
    reports = both_teams_for(["a2", "a3", "b1", "b2", "b3"], TEAM_A)
    reports.append(rep("a1", TEAM_A, kda=bad))
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_KDA_INTERNAL_INCONSISTENCY in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Adversarial: schema-version disagreement                                     #
# --------------------------------------------------------------------------- #
def test_schema_minority_still_accepts() -> None:
    reports = [rep(p, TEAM_A, schema_version="v5") for p in ("a1", "a2", "a3", "a4", "b1", "b2")]
    reports.append(rep("b3", TEAM_A, schema_version="v4"))
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert FLAG_SCHEMA_VERSION_MINORITY in result.anomaly_flags


def test_schema_fleet_wide_split_blocks_accept() -> None:
    a = [rep(p, TEAM_A, schema_version="v1") for p in ("a1", "a2", "b1", "b2")]
    b = [rep(p, TEAM_A, schema_version="v2") for p in ("a3", "a4", "b3", "b4")]
    result = adjudicate_result(a + b, ROSTER)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_SCHEMA_VERSION_DISAGREEMENT in result.anomaly_flags


# --------------------------------------------------------------------------- #
# Optional corroborators (default OFF)                                         #
# --------------------------------------------------------------------------- #
def test_nonce_replay_disqualifies_to_shortfall() -> None:
    nonces = {p: "good" for p in ("a1", "a2", "a3", "b1", "b2", "b3")}
    reports = [rep(p, TEAM_A, nonce="good") for p in ("a1", "a2", "a3")]
    reports += [rep(p, TEAM_A, nonce="stale") for p in ("b1", "b2", "b3")]
    result = adjudicate_result(reports, ROSTER, valid_nonces=nonces)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.reports_counted == 3  # the 3 replays disqualified


def test_killstream_contradiction_disputes() -> None:
    reports = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4"], TEAM_A)
    ks = {"G1": KillStreamSummary(game_id="G1", kills_by_puuid={}, implied_winner=TEAM_B)}
    result = adjudicate_result(reports, ROSTER, killstream_by_game=ks)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_KILLSTREAM_CONTRADICTION in result.anomaly_flags


def test_optional_corroborators_default_off() -> None:
    reports = both_teams_for(A[:5] + B, TEAM_A)
    result = adjudicate_result(reports, ROSTER)
    assert result.outcome is AdjudicationOutcome.ACCEPT
    assert result.anomaly_flags == []


# --------------------------------------------------------------------------- #
# Roster preconditions                                                         #
# --------------------------------------------------------------------------- #
def test_roster_overlap_disputes() -> None:
    roster = {
        TEAM_A: frozenset({"a1", "a2", "a3", "a4", "shared"}),
        TEAM_B: frozenset({"b1", "b2", "b3", "b4", "shared"}),
    }
    reports = both_teams_for(["a1", "a2", "a3", "b1", "b2", "shared"], TEAM_A)
    result = adjudicate_result(reports, roster)
    assert result.outcome is AdjudicationOutcome.DISPUTE
    assert FLAG_ROSTER_OVERLAP in result.anomaly_flags


def test_roster_misconfigured_three_teams_is_insufficient() -> None:
    roster = {TEAM_A: frozenset(A[:5]), TEAM_B: frozenset(B), TEAM_C: frozenset({"c1"})}
    reports = both_teams_for(A[:5] + B, TEAM_A)
    result = adjudicate_result(reports, roster)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT
    assert result.anomaly_flags == [FLAG_ROSTER_MISCONFIGURED]


def test_roster_empty_side_is_insufficient() -> None:
    roster = {TEAM_A: frozenset(A[:5]), TEAM_B: frozenset()}
    result = adjudicate_result(both_teams_for(A[:5], TEAM_A), roster)
    assert result.outcome is AdjudicationOutcome.INSUFFICIENT


# --------------------------------------------------------------------------- #
# Invariants: determinism, totality, immutability, boundaries                 #
# --------------------------------------------------------------------------- #
def test_quorum_threshold_off_by_one() -> None:
    five = both_teams_for(["a1", "a2", "a3", "b1", "b2"], TEAM_A) + [rep("a4"), rep("b3")]
    assert adjudicate_result(five, ROSTER).outcome is AdjudicationOutcome.INSUFFICIENT
    six = both_teams_for(["a1", "a2", "a3", "b1", "b2", "b3"], TEAM_A)
    assert adjudicate_result(six, ROSTER).outcome is AdjudicationOutcome.ACCEPT


def test_custom_quorum_threshold_honored() -> None:
    seven = both_teams_for(["a1", "a2", "a3", "a4", "b1", "b2", "b3"], TEAM_A)
    assert (
        adjudicate_result(seven, ROSTER, quorum_threshold=8).outcome
        is AdjudicationOutcome.INSUFFICIENT
    )
    eight = seven + [rep("a5", TEAM_A)]
    assert (
        adjudicate_result(eight, ROSTER, quorum_threshold=8).outcome is AdjudicationOutcome.ACCEPT
    )


def test_report_order_independence() -> None:
    base = both_teams_for(["a1", "a2", "a3", "b1", "b2", "b3"], TEAM_A) + [
        rep("a4"),
        rep("b4", TEAM_B),
    ]
    expected = adjudicate_result(base, ROSTER)
    rng = random.Random(1234)
    for _ in range(6):
        shuffled = base[:]
        rng.shuffle(shuffled)
        got = adjudicate_result(shuffled, ROSTER)
        assert got.outcome is expected.outcome
        assert got.winner_team_id == expected.winner_team_id
        assert got.agreed_game_id == expected.agreed_game_id
        assert got.anomaly_flags == expected.anomaly_flags
        assert got.reports_counted == expected.reports_counted


def test_dedupe_tied_timestamp_survivor_is_order_independent() -> None:
    # Regression (review finding): two same-key duplicate rows with tied recency
    # (received_at=None, id=None) but different KDA must pick the SAME survivor
    # regardless of input order — else the survivor's fingerprint, the
    # byte-identical check, and the terminal verdict flip on reorder.
    k1 = [PlayerKDA(puuid="a1", kills=1, deaths=2, assists=3)]
    k2 = [PlayerKDA(puuid="a1", kills=5, deaths=6, assists=7)]
    dup_a = rep("a1", TEAM_A, kda=k1)
    dup_b = rep("a1", TEAM_A, kda=k2)  # same (game, puuid, source) key, same winner
    rest = both_teams_for(["a2", "a3", "b1", "b2", "b3"], TEAM_A)
    collide = rep("a2", TEAM_A, kda=k1)  # would collide with dup_a's fingerprint
    base = [dup_a, dup_b, collide, *rest[1:]]
    expected = adjudicate_result(base, ROSTER)
    rng = random.Random(99)
    for _ in range(8):
        shuffled = base[:]
        rng.shuffle(shuffled)
        got = adjudicate_result(shuffled, ROSTER)
        assert got.outcome is expected.outcome
        assert got.anomaly_flags == expected.anomaly_flags
        assert got.winner_team_id == expected.winner_team_id


def test_winner_id_none_on_every_non_accept() -> None:
    cases = [
        adjudicate_result([], ROSTER),
        adjudicate_result(both_teams_for(["a1", "b1"], TEAM_A), ROSTER),
        adjudicate_result(both_teams_for(A[:5], TEAM_A) + both_teams_for(B, TEAM_B), ROSTER),
    ]
    for r in cases:
        if r.outcome is not AdjudicationOutcome.ACCEPT:
            assert r.winner_team_id is None


def test_function_total_never_raises_on_garbage() -> None:
    weird = [
        rep("a1"),  # None winner
        rep("b1", TEAM_C),  # off-match winner
        rep("x9", TEAM_A),  # off-roster
        rep("a2", TEAM_A, schema_version=None),
    ]
    result = adjudicate_result(weird, ROSTER)
    assert isinstance(result, AdjudicationResult)


def test_result_is_frozen() -> None:
    r = adjudicate_result([], ROSTER)
    with pytest.raises(FrozenInstanceError):
        r.outcome = AdjudicationOutcome.ACCEPT  # type: ignore[misc]


def test_anomaly_flags_sorted_unique_and_known() -> None:
    reports = both_teams_for(A[:5], TEAM_A) + both_teams_for(B, TEAM_B)
    flags = adjudicate_result(reports, ROSTER).anomaly_flags
    assert flags == sorted(flags)
    assert len(flags) == len(set(flags))
    assert set(flags) <= ANOMALY_FLAGS


# --------------------------------------------------------------------------- #
# 1v1 duel (ARAM): only 2 reporters, so a mode-aware quorum/total is required  #
# --------------------------------------------------------------------------- #
_DUEL_ROSTER = {TEAM_A: frozenset({"a1"}), TEAM_B: frozenset({"b1"})}


def test_1v1_both_agree_accepts_with_2_of_2() -> None:
    # Both duelists report A won; 2/2 quorum + total=2 resolves it (6/10 can't).
    reports = [rep("a1", TEAM_A), rep("b1", TEAM_A)]
    result = adjudicate_result(reports, _DUEL_ROSTER, quorum_threshold=2, total_participants=2)
    assert result.outcome == AdjudicationOutcome.ACCEPT
    assert result.winner_team_id == TEAM_A


def test_1v1_single_report_is_insufficient_not_dispute() -> None:
    # One duelist still outstanding -> benign capture race, not a dispute.
    result = adjudicate_result(
        [rep("a1", TEAM_A)], _DUEL_ROSTER, quorum_threshold=2, total_participants=2
    )
    assert result.outcome == AdjudicationOutcome.INSUFFICIENT


def test_1v1_conflicting_claims_dispute() -> None:
    # Each claims they won -> both reports in, no 2/2 agreement -> DISPUTE.
    reports = [rep("a1", TEAM_A), rep("b1", TEAM_B)]
    result = adjudicate_result(reports, _DUEL_ROSTER, quorum_threshold=2, total_participants=2)
    assert result.outcome == AdjudicationOutcome.DISPUTE


# ---------------------------------------------------------------------------
# derive_first_blood_team (GameMode.decides_on_first_blood — §3.6.4)
# ---------------------------------------------------------------------------
_FB_NAMES = {"alice": "puuid-a", "alice#eune": "puuid-a", "bob": "puuid-b", "bob#eune": "puuid-b"}
_FB_ROSTER: dict[UUID, frozenset[str]] = {
    TEAM_A: frozenset({"puuid-a"}),
    TEAM_B: frozenset({"puuid-b"}),
}


def _ck(killer: str, victim: str, *, at: float, eid: int = 10) -> dict[str, object]:
    return {
        "eventId": eid,
        "eventName": "ChampionKill",
        "eventTime": at,
        "killerName": killer,
        "victimName": victim,
    }


def test_first_blood_recipient_names_the_scorer() -> None:
    events = [{"eventId": 3, "eventName": "FirstBlood", "eventTime": 90.0, "recipient": "Bob"}]
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) == TEAM_B


def test_first_blood_pairs_with_the_same_tick_champion_kill() -> None:
    # No Recipient (older capture): the killer of the same-tick ChampionKill scores.
    events = [
        {"eventId": 1, "eventName": "GameStart", "eventTime": 0.0},
        _ck("Alice", "Bob", at=90.0),
        {"eventId": 11, "eventName": "FirstBlood", "eventTime": 90.0},
        _ck("Bob", "Alice", at=200.0, eid=20),  # a later kill must not matter
    ]
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) == TEAM_A


def test_first_blood_skips_a_turret_execute() -> None:
    # An execute (unmappable killer) precedes the real first blood: FirstBlood
    # fires with the LATER champion kill and must pair with it, not the execute.
    events = [
        _ck("Turret_T2_C_05_A", "Alice", at=60.0, eid=5),
        _ck("Bob", "Alice", at=180.0, eid=9),
        {"eventId": 10, "eventName": "FirstBlood", "eventTime": 180.0},
    ]
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) == TEAM_B


def test_no_first_blood_event_casts_no_vote() -> None:
    # A ChampionKill alone (e.g. a capture from a mode where FB was disabled, or a
    # forged stream missing the pair) never votes without the FirstBlood marker.
    assert derive_first_blood_team([_ck("Alice", "Bob", at=90.0)], _FB_NAMES, _FB_ROSTER) is None
    assert derive_first_blood_team([], _FB_NAMES, _FB_ROSTER) is None


def test_first_blood_riot_id_tag_falls_back_to_game_name() -> None:
    events = [{"eventId": 3, "eventName": "FirstBlood", "eventTime": 9.0, "recipient": "Bob#NA1"}]
    # "bob#na1" is not a key, but the pre-# game name "bob" is.
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) == TEAM_B


def test_first_blood_unmappable_or_malformed_is_total() -> None:
    # Unknown names, junk entries, and bad field types must yield None, not raise.
    events: list[dict[str, object]] = [
        {"eventId": "x", "eventName": "FirstBlood", "eventTime": "y", "recipient": "Nobody"},
        {"eventName": 42},
        {"eventId": 2, "eventName": "ChampionKill", "eventTime": 1.0, "killerName": "Stranger"},
    ]
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) is None


def test_first_blood_far_away_kill_does_not_pair() -> None:
    # The only mappable kill is far outside the pairing tolerance: no vote is
    # safer than guessing (the FB pair should have been captured together).
    events = [
        {"eventId": 1, "eventName": "FirstBlood", "eventTime": 10.0},
        _ck("Bob", "Alice", at=300.0),
    ]
    assert derive_first_blood_team(events, _FB_NAMES, _FB_ROSTER) is None
