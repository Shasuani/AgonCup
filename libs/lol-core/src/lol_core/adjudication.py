"""Result quorum / anomaly adjudication (PLAN.md §3.6.6).

The result-corroboration core. A match has up to 10 forgeable, identity-bound
per-participant reports; this module turns them into one of three terminal
verdicts (``ACCEPT`` / ``DISPUTE`` / ``INSUFFICIENT``) plus an audit trail.

Trust model (documented threat model from PLAN.md §3.6.6 / §9): every
``ResultReportDTO`` is an unsigned, user-attestable JSON blob captured over a
self-signed local LCU cert with user-controlled credentials — **fully forgeable
and adversarial**. No local source is a trust anchor. Integrity rests on
cross-checking the (up to 10) independent reports keyed by a pre-bound
``lcu_game_id`` with **both teams represented**, an anomaly pipeline, and a
bounded admin override on every branch.

Design invariants (load-bearing — keep them true):

* **Pure & framework-light.** No DB, no IO, no clock. The optional corroborators
  (``used_game_ids`` / ``valid_nonces`` / ``killstream_by_game``) are injected and
  default OFF so the core stays offline-unit-testable; the worker/ingress phase
  wires the real sources without a contract bump.
* **Total.** Never raises on adversarial input — always returns an
  ``AdjudicationResult``.
* **Deterministic.** Output is independent of input report ordering: every set
  iteration that affects the result is sorted on a stable key.
* **Identity-bound counting.** A reporter is counted at most ONCE no matter how
  many sources / duplicate rows it submits (anti source-inflation).
* **Never penalize absence.** A missing report is a benign capture-race signal
  (``INSUFFICIENT``), never an anomaly; only forged/contradictory *present* data
  escalates to ``DISPUTE``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from enum import StrEnum
from uuid import UUID

from lol_schemas import ResultReportDTO
from lol_schemas.enums import ResultSource

# The both-teams-represented quorum floor (PLAN.md §3.6.6 mitigation 1): a result
# auto-accepts only when at least this many distinct identity-bound reports agree
# on the same agreed_game_id and winning team AND >=1 agreeing report comes from
# each registered roster. Modest floor (not 8/10) to tolerate ~4 honest-missing
# reports (capture race) without defeating autonomy.
QUORUM_THRESHOLD = 6
TOTAL_PARTICIPANTS = 10


# ---------------------------------------------------------------------------
# Anomaly flag vocabulary (PLAN.md §3.6.6). Module-level string constants so the
# worker serializer + admin UI reference symbols, not literals. The flag CODES
# are the public audit spine (serialized verbatim into QuorumDetail.anomaly_summary);
# their SEVERITY stays internal to this module.
# ---------------------------------------------------------------------------
FLAG_ROSTER_MISCONFIGURED = "roster_misconfigured"
FLAG_ROSTER_OVERLAP = "roster_overlap"
FLAG_DUPLICATE_CONFLICTING_REPORT = "duplicate_conflicting_report"
FLAG_OVERSUBSCRIBED_REPORTS = "oversubscribed_reports"
FLAG_OUTSIDER_REPORT = "outsider_report"
FLAG_GAME_ID_SPLIT = "game_id_split"
FLAG_REUSED_GAME_ID = "reused_game_id"
FLAG_WINNER_NOT_IN_MATCH = "winner_not_in_match"
FLAG_ASYMMETRIC_WINNER_CLAIM = "asymmetric_winner_claim"
FLAG_MINOR_WINNER_DISSENT = "minor_winner_dissent"
FLAG_ONE_SIDED_QUORUM = "one_sided_quorum"
FLAG_BYTE_IDENTICAL_PAYLOAD = "byte_identical_payload"
FLAG_KDA_INTERNAL_INCONSISTENCY = "kda_internal_inconsistency"
FLAG_KILLSTREAM_CONTRADICTION = "killstream_contradiction"
FLAG_SCHEMA_VERSION_DISAGREEMENT = "schema_version_disagreement"
FLAG_SCHEMA_VERSION_MINORITY = "schema_version_minority"
FLAG_NONCE_FRESHNESS_INVALID = "nonce_freshness_invalid"
FLAG_MISSING_REPORTS = "missing_reports"

#: Every flag this module can emit (stable audit vocabulary for downstream).
ANOMALY_FLAGS: frozenset[str] = frozenset(
    {
        FLAG_ROSTER_MISCONFIGURED,
        FLAG_ROSTER_OVERLAP,
        FLAG_DUPLICATE_CONFLICTING_REPORT,
        FLAG_OVERSUBSCRIBED_REPORTS,
        FLAG_OUTSIDER_REPORT,
        FLAG_GAME_ID_SPLIT,
        FLAG_REUSED_GAME_ID,
        FLAG_WINNER_NOT_IN_MATCH,
        FLAG_ASYMMETRIC_WINNER_CLAIM,
        FLAG_MINOR_WINNER_DISSENT,
        FLAG_ONE_SIDED_QUORUM,
        FLAG_BYTE_IDENTICAL_PAYLOAD,
        FLAG_KDA_INTERNAL_INCONSISTENCY,
        FLAG_KILLSTREAM_CONTRADICTION,
        FLAG_SCHEMA_VERSION_DISAGREEMENT,
        FLAG_SCHEMA_VERSION_MINORITY,
        FLAG_NONCE_FRESHNESS_INVALID,
        FLAG_MISSING_REPORTS,
    }
)


class AdjudicationOutcome(StrEnum):
    """The three terminal verdicts of the quorum/anomaly step.

    * ``ACCEPT`` — quorum met (>=6/10 agree on game id + winner, both teams
      represented, no high-severity anomaly): mark the ``MatchResult``
      ``provisional`` and advance the bracket.
    * ``DISPUTE`` — adversarial signal (asymmetric/contradictory winner claims,
      KDA contradicting the corroborated kill stream, byte-identical payloads,
      duplicate/split game id, ...): freeze advancement, escalate to the
      anomaly-ranked admin override queue.
    * ``INSUFFICIENT`` — benign capture-race shortfall (too few reports, or quorum
      reachable but not yet present): re-request within the persistence window,
      do NOT advance and do NOT penalize the missing reporters.
    """

    ACCEPT = "accept"
    DISPUTE = "dispute"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class AdjudicationResult:
    """The outcome of adjudication plus the audit trail for the admin queue."""

    outcome: AdjudicationOutcome
    winner_team_id: UUID | None = None
    agreed_game_id: str | None = None
    reports_counted: int = 0
    both_teams_represented: bool = False
    anomaly_flags: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class KillStreamSummary:
    """An independent kill-stream view of one game, for the optional cross-check.

    Injected by the worker/ingress phase from corroborated ``:2999`` / live-client
    captures (default OFF here so the core stays pure). ``implied_winner`` is the
    team the kill stream points at; a mismatch with the report quorum is a
    high-severity anomaly.
    """

    game_id: str
    kills_by_puuid: Mapping[str, int]
    implied_winner: UUID | None = None


# ---------------------------------------------------------------------------
# Internal severity model (module-private; NOT exported, NOT in lol_schemas).
# ---------------------------------------------------------------------------
class _Severity(StrEnum):
    HIGH = "high"  # forces DISPUTE
    BLOCK = "block"  # blocks ACCEPT (downgrades a would-be accept to dispute)
    LOW = "low"  # audit only; never changes the outcome


@dataclass(frozen=True)
class _Flag:
    code: str
    severity: _Severity


@dataclass(frozen=True)
class _Vote:
    """One identity-deduped, qualified report: at most one per reporter PUUID."""

    reporter_puuid: str
    lcu_game_id: str
    winner: UUID | None
    schema_version: str | None
    fingerprint: tuple[object, ...] | None


@dataclass(frozen=True)
class _Candidate:
    winner: UUID
    reporter_puuids: frozenset[str]
    teams_represented: frozenset[UUID]

    @property
    def agree_count(self) -> int:
        return len(self.reporter_puuids)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def adjudicate_result(
    reports: Sequence[ResultReportDTO],
    roster_by_team: Mapping[UUID, frozenset[str]],
    *,
    quorum_threshold: int = QUORUM_THRESHOLD,
    used_game_ids: frozenset[str] = frozenset(),
    valid_nonces: Mapping[str, str] | None = None,
    killstream_by_game: Mapping[str, KillStreamSummary] | None = None,
) -> AdjudicationResult:
    """Adjudicate a match result from up to 10 forgeable participant reports.

    Pipeline (PLAN.md §3.6.6), top to bottom; the first decisive condition wins:

    0. **Roster preconditions.** A non-1v1 / empty roster is a wiring fault, not
       forgery → ``INSUFFICIENT`` (no penalty). Rosters sharing a PUUID is a
       self-corroboration vector → high anomaly.
    1. **Identity dedupe.** Collapse ``(lcu_game_id, reporter_puuid, source)``
       duplicates (keep the latest), then collapse the same PUUID across sources
       to ONE vote — so a client cannot inflate the count by double-reporting.
    2. **Disqualify** structurally-impossible (bad KDA) or stale-nonce votes,
       degrading the vote to *absent* (never counted) rather than disputing.
    3. **Game-id binding.** Require a single ``lcu_game_id``; a split or a reused
       id is a high anomaly.
    4. **Winner clustering + anomaly pipeline.** Group by claimed winner among
       on-roster votes on the dominant game; run the full anomaly set
       (asymmetric claims, byte-identical payloads, kill-stream contradiction,
       schema disagreement, ...).
    5. **Reachability.** A sub-quorum result is a benign capture race
       (``INSUFFICIENT``) while reports are still outstanding, but an
       all-reports-in stalemate escalates to ``DISPUTE``.
    6. **Verdict.** Quorum met & clean → ``ACCEPT``; any high anomaly →
       ``DISPUTE``; otherwise the reachability split. Admin override is the final
       escape hatch on every branch.

    Args:
        reports: the (up to 10) identity-bound, forgeable participant reports.
        roster_by_team: registered PUUIDs per team id (exactly two for a 1v1);
            the both-teams-represented authority. Sourced from registration/
            check-in, NEVER from the reports themselves.
        quorum_threshold: agreeing-report floor (default 6).
        used_game_ids: game ids already consumed by a prior match (reuse → high
            anomaly). Default empty (off).
        valid_nonces: ``reporter_puuid -> expected single-use nonce``; a report
            with a missing/mismatched nonce is disqualified. Default ``None`` (off).
        killstream_by_game: ``game_id -> KillStreamSummary`` independent witness;
            a contradiction with the report quorum is a high anomaly. Default
            ``None`` (off).

    Returns:
        An :class:`AdjudicationResult` carrying the outcome + audit trail. Never
        raises.
    """
    flags: set[_Flag] = set()

    # --- Stage 0: roster preconditions ------------------------------------
    roster_team_ids = frozenset(roster_by_team.keys())
    if len(roster_by_team) != 2 or any(len(p) == 0 for p in roster_by_team.values()):
        return AdjudicationResult(
            outcome=AdjudicationOutcome.INSUFFICIENT,
            anomaly_flags=[FLAG_ROSTER_MISCONFIGURED],
            detail="insufficient: roster misconfigured (expected two non-empty rosters)",
        )

    teams = sorted(roster_by_team.keys(), key=str)
    overlap = roster_by_team[teams[0]] & roster_by_team[teams[1]]
    if overlap:
        flags.add(_Flag(FLAG_ROSTER_OVERLAP, _Severity.HIGH))
    # A PUUID on both rosters can prove neither side — drop it from the index.
    puuid_to_team: dict[str, UUID] = {}
    for team, puuids in roster_by_team.items():
        for puuid in puuids:
            if puuid not in overlap:
                puuid_to_team[puuid] = team

    # --- Stage 1: identity dedupe -----------------------------------------
    votes, dedupe_flags = _identity_dedupe(reports)
    flags.update(dedupe_flags)

    distinct_reporters_seen = len(votes)
    if distinct_reporters_seen > TOTAL_PARTICIPANTS:
        flags.add(_Flag(FLAG_OVERSUBSCRIBED_REPORTS, _Severity.HIGH))

    # --- Stage 2: disqualify (degrade-to-absent), partition on/off roster --
    qualified: list[_Vote] = []
    for v in votes:
        if _kda_inconsistent_by_puuid(reports, v.reporter_puuid, roster_team_ids):
            flags.add(_Flag(FLAG_KDA_INTERNAL_INCONSISTENCY, _Severity.HIGH))
            continue  # disqualified — never counted
        if valid_nonces is not None and not _nonce_ok(reports, v.reporter_puuid, valid_nonces):
            flags.add(_Flag(FLAG_NONCE_FRESHNESS_INVALID, _Severity.BLOCK))
            continue  # disqualified — never counted
        qualified.append(v)

    countable = [v for v in qualified if v.reporter_puuid in puuid_to_team]
    if any(v.reporter_puuid not in puuid_to_team for v in qualified):
        flags.add(_Flag(FLAG_OUTSIDER_REPORT, _Severity.LOW))

    if len({v.reporter_puuid for v in countable}) < TOTAL_PARTICIPANTS:
        flags.add(_Flag(FLAG_MISSING_REPORTS, _Severity.LOW))

    # --- Stage 3: game-id binding -----------------------------------------
    dominant_game_id = _dominant_game_id(countable)
    if len({v.lcu_game_id for v in countable}) > 1:
        flags.add(_Flag(FLAG_GAME_ID_SPLIT, _Severity.HIGH))
    if dominant_game_id is not None and dominant_game_id in used_game_ids:
        flags.add(_Flag(FLAG_REUSED_GAME_ID, _Severity.HIGH))

    on_game = [v for v in countable if v.lcu_game_id == dominant_game_id]

    # --- Stage 4: winner clustering + anomaly pipeline --------------------
    candidates, winner_flags = _winner_candidates(on_game, puuid_to_team, roster_team_ids)
    flags.update(winner_flags)
    best = candidates[0] if candidates else None

    # Asymmetric winner claim: >=2 distinct winners each with real support (>=2).
    strong = [c for c in candidates if c.agree_count >= 2]
    if len(strong) >= 2:
        flags.add(_Flag(FLAG_ASYMMETRIC_WINNER_CLAIM, _Severity.HIGH))
    elif best is not None and any(c.agree_count == 1 for c in candidates[1:]):
        flags.add(_Flag(FLAG_MINOR_WINNER_DISSENT, _Severity.LOW))

    # Byte-identical payloads: >=2 distinct PUUIDs sharing a non-empty fingerprint.
    bi_puuids = _byte_identical_puuids(on_game)
    if bi_puuids:
        # Load-bearing escalation: HIGH only if removing the cloned reports drops
        # the leading candidate below quorum or breaks both-teams; else MEDIUM audit.
        load_bearing = _is_load_bearing(
            best, bi_puuids, puuid_to_team, roster_team_ids, quorum_threshold
        )
        flags.add(
            _Flag(FLAG_BYTE_IDENTICAL_PAYLOAD, _Severity.HIGH if load_bearing else _Severity.LOW)
        )

    # Kill-stream contradiction (optional witness): implied winner != quorum winner.
    if killstream_by_game is not None and dominant_game_id is not None and best is not None:
        ks = killstream_by_game.get(dominant_game_id)
        if ks is not None and ks.implied_winner is not None and ks.implied_winner != best.winner:
            flags.add(_Flag(FLAG_KILLSTREAM_CONTRADICTION, _Severity.HIGH))

    # Schema-version agreement: a fleet with no majority version blocks ACCEPT.
    flags.update(_schema_flags(on_game))

    # --- Stage 5/6: reachability + verdict --------------------------------
    return _verdict(
        best=best,
        roster_team_ids=roster_team_ids,
        flags=flags,
        quorum_threshold=quorum_threshold,
        dominant_game_id=dominant_game_id,
        distinct_reporters_seen=distinct_reporters_seen,
    )


# ---------------------------------------------------------------------------
# Stage helpers (all pure)
# ---------------------------------------------------------------------------
def _identity_dedupe(reports: Sequence[ResultReportDTO]) -> tuple[list[_Vote], set[_Flag]]:
    """Collapse duplicate rows to one canonical vote per reporter PUUID.

    (a) Natural-key dedupe on ``(lcu_game_id, reporter_puuid, source)`` keeping the
        latest ``received_at``; surviving rows for one key disagreeing on winner is
        a high anomaly (tampering). (b) Collapse the same PUUID across sources to a
        single vote, preferring ``EOG_STATS_BLOCK`` over ``LIVE_CLIENT_DATA``.
    """
    flags: set[_Flag] = set()

    # (a) Group by natural key; detect conflicts; keep the latest received_at.
    by_key: dict[tuple[str, str, str], list[ResultReportDTO]] = {}
    for r in reports:
        by_key.setdefault((r.lcu_game_id, r.reporter_puuid, r.source.value), []).append(r)

    survivors: list[ResultReportDTO] = []
    for rows in by_key.values():
        if len({r.winner for r in rows}) > 1:
            flags.add(_Flag(FLAG_DUPLICATE_CONFLICTING_REPORT, _Severity.HIGH))
        # Keep the latest received_at; the content key is a TOTAL tie-break so the
        # survivor is independent of input order (else a forged duplicate with a
        # tied timestamp could flip the fingerprint — and the verdict — by reorder).
        survivors.append(max(rows, key=lambda r: (_recency_ts(r), _content_key(r))))

    # (b) Collapse across sources → one vote per reporter PUUID (prefer EOG). The
    # content key again totally orders ties (e.g. the same PUUID on >1 game id).
    by_puuid: dict[str, list[ResultReportDTO]] = {}
    for r in survivors:
        by_puuid.setdefault(r.reporter_puuid, []).append(r)

    votes: list[_Vote] = []
    for puuid in sorted(by_puuid):
        chosen = min(by_puuid[puuid], key=lambda r: (_source_rank(r), _content_key(r)))
        votes.append(
            _Vote(
                reporter_puuid=puuid,
                lcu_game_id=chosen.lcu_game_id,
                winner=chosen.winner,
                schema_version=chosen.schema_version,
                fingerprint=_fingerprint(chosen),
            )
        )
    return votes, flags


def _recency_ts(r: ResultReportDTO) -> float:
    """Epoch seconds for keep-latest dedupe; missing timestamp sorts oldest.

    Naive datetimes are read as UTC so mixed naive/aware timestamps never raise on
    comparison.
    """
    if r.received_at is None:
        return float("-inf")
    dt = r.received_at if r.received_at.tzinfo is not None else r.received_at.replace(tzinfo=UTC)
    return dt.timestamp()


def _source_rank(r: ResultSource | ResultReportDTO) -> int:
    """0 for EOG, 1 otherwise — prefer EOG when one PUUID reported via both."""
    source = r.source if isinstance(r, ResultReportDTO) else r
    return 0 if source == ResultSource.EOG_STATS_BLOCK else 1


def _content_key(r: ResultReportDTO) -> tuple[object, ...]:
    """A TOTAL, input-order-independent ordering of a report by its content.

    Used only as a deterministic tie-break when two rows share a recency/source
    rank — guarantees the dedupe survivor (and thus the fingerprint and verdict)
    cannot change with report ordering.
    """
    kda = tuple(sorted((k.puuid, k.kills, k.deaths, k.assists) for k in r.per_player_kda))
    return (
        str(r.id) if r.id is not None else "",
        r.source.value,
        r.lcu_game_id,
        str(r.winner),
        r.schema_version or "",
        r.nonce or "",
        kda,
    )


def _fingerprint(r: ResultReportDTO) -> tuple[object, ...] | None:
    """Identity-stripped content hash; ``None`` when there is no KDA to compare.

    Excludes ``reporter_puuid`` / ``nonce`` / ``received_at`` / ``source`` so two
    clients submitting a byte-identical scoreboard collide. An empty KDA block is
    not enough signal to call two reports "identical".
    """
    if not r.per_player_kda:
        return None
    kda = tuple(sorted((k.puuid, k.kills, k.deaths, k.assists) for k in r.per_player_kda))
    return (str(r.winner), r.lcu_game_id, r.schema_version, kda)


def _kda_inconsistent_by_puuid(
    reports: Sequence[ResultReportDTO], puuid: str, roster_team_ids: frozenset[UUID]
) -> bool:
    """True if ANY row from this reporter has a structurally impossible KDA block."""
    for r in reports:
        if r.reporter_puuid != puuid:
            continue
        listed = [k.puuid for k in r.per_player_kda]
        if len(listed) != len(set(listed)) or len(listed) > TOTAL_PARTICIPANTS:
            return True
    return False


def _nonce_ok(
    reports: Sequence[ResultReportDTO], puuid: str, valid_nonces: Mapping[str, str]
) -> bool:
    """True if at least one of this reporter's rows carries the expected nonce."""
    expected = valid_nonces.get(puuid)
    if expected is None:
        return False
    return any(r.reporter_puuid == puuid and r.nonce == expected for r in reports)


def _dominant_game_id(countable: Sequence[_Vote]) -> str | None:
    """The most-reported game id (lexicographic tie-break); ``None`` if no votes."""
    counts = Counter(v.lcu_game_id for v in countable)
    if not counts:
        return None
    top = max(counts.values())
    return min(gid for gid, n in counts.items() if n == top)


def _winner_candidates(
    on_game: Sequence[_Vote],
    puuid_to_team: Mapping[str, UUID],
    roster_team_ids: frozenset[UUID],
) -> tuple[list[_Candidate], set[_Flag]]:
    """Group on-game votes by claimed winner into ranked candidates.

    A claimed winner not among the registered team ids is a high anomaly and its
    reports are excluded from candidacy. ``None``-winner votes form no candidate.
    Candidates are sorted by descending support then ``str(winner)`` for
    determinism.
    """
    flags: set[_Flag] = set()
    by_winner: dict[UUID, set[str]] = {}
    for v in on_game:
        if v.winner is None:
            continue
        if v.winner not in roster_team_ids:
            flags.add(_Flag(FLAG_WINNER_NOT_IN_MATCH, _Severity.HIGH))
            continue
        by_winner.setdefault(v.winner, set()).add(v.reporter_puuid)

    candidates = [
        _Candidate(
            winner=winner,
            reporter_puuids=frozenset(puuids),
            teams_represented=frozenset(puuid_to_team[p] for p in puuids if p in puuid_to_team),
        )
        for winner, puuids in by_winner.items()
    ]
    candidates.sort(key=lambda c: (-c.agree_count, str(c.winner)))
    return candidates, flags


def _byte_identical_puuids(on_game: Sequence[_Vote]) -> frozenset[str]:
    """PUUIDs whose report shares a non-empty fingerprint with another reporter."""
    by_fp: dict[tuple[object, ...], set[str]] = {}
    for v in on_game:
        if v.fingerprint is not None:
            by_fp.setdefault(v.fingerprint, set()).add(v.reporter_puuid)
    suspect: set[str] = set()
    for puuids in by_fp.values():
        if len(puuids) >= 2:
            suspect |= puuids
    return frozenset(suspect)


def _is_load_bearing(
    best: _Candidate | None,
    suspect: frozenset[str],
    puuid_to_team: Mapping[str, UUID],
    roster_team_ids: frozenset[UUID],
    quorum_threshold: int,
) -> bool:
    """True if removing the cloned reports would drop the leader below quorum."""
    if best is None:
        return False
    remaining = best.reporter_puuids - suspect
    teams = frozenset(puuid_to_team[p] for p in remaining if p in puuid_to_team)
    both = teams == roster_team_ids
    return best.agree_count >= quorum_threshold and (len(remaining) < quorum_threshold or not both)


def _schema_flags(on_game: Sequence[_Vote]) -> set[_Flag]:
    """Schema-version agreement check over the on-game votes.

    A clear majority version with a small minority is benign (LOW). A fleet with
    no majority version (e.g. an even split) blocks ACCEPT — we will not auto-accept
    a result the clients may have parsed under incompatible schemas.
    """
    versions = [v.schema_version for v in on_game if v.schema_version is not None]
    if len(versions) < 2:
        return set()
    counts = Counter(versions)
    if len(counts) < 2:
        return set()
    top = max(counts.values())
    if top * 2 > len(versions):
        return {_Flag(FLAG_SCHEMA_VERSION_MINORITY, _Severity.LOW)}
    return {_Flag(FLAG_SCHEMA_VERSION_DISAGREEMENT, _Severity.BLOCK)}


def _verdict(
    *,
    best: _Candidate | None,
    roster_team_ids: frozenset[UUID],
    flags: set[_Flag],
    quorum_threshold: int,
    dominant_game_id: str | None,
    distinct_reporters_seen: int,
) -> AdjudicationResult:
    """The decision table — top-down, first match wins."""
    high = {f.code for f in flags if f.severity is _Severity.HIGH}
    block = {f.code for f in flags if f.severity is _Severity.BLOCK}
    codes = sorted({f.code for f in flags})

    agree = best.agree_count if best is not None else 0
    both_teams = best.teams_represented == roster_team_ids if best is not None else False
    winner = best.winner if best is not None else None
    quorum_met = best is not None and agree >= quorum_threshold and both_teams
    # Still-outstanding reports mean the picture can change → a shortfall is benign.
    reachable = distinct_reporters_seen < TOTAL_PARTICIPANTS

    # 1. A clean both-teams quorum auto-resolves — unless something escalated it.
    if quorum_met and not high and not block:
        return AdjudicationResult(
            outcome=AdjudicationOutcome.ACCEPT,
            winner_team_id=winner,
            agreed_game_id=dominant_game_id,
            reports_counted=agree,
            both_teams_represented=True,
            anomaly_flags=codes,
            detail=f"accept: quorum {agree}/{TOTAL_PARTICIPANTS} for {winner}, both teams",
        )

    # 2. Any high-severity anomaly freezes advancement → admin queue.
    if high:
        return AdjudicationResult(
            outcome=AdjudicationOutcome.DISPUTE,
            agreed_game_id=dominant_game_id,
            reports_counted=agree,
            both_teams_represented=both_teams,
            anomaly_flags=codes,
            detail=f"dispute: high-severity anomaly ({', '.join(sorted(high))})",
        )

    # 3. A would-be quorum blocked by a soft signal (e.g. schema disagreement).
    if quorum_met and block:
        return AdjudicationResult(
            outcome=AdjudicationOutcome.DISPUTE,
            agreed_game_id=dominant_game_id,
            reports_counted=agree,
            both_teams_represented=both_teams,
            anomaly_flags=codes,
            detail=f"dispute: quorum reached but blocked ({', '.join(sorted(block))})",
        )

    # 4. A >=quorum one-sided cluster: benign while reachable, suspicious when all in.
    if best is not None and agree >= quorum_threshold and not both_teams:
        flag = sorted(codes + [FLAG_ONE_SIDED_QUORUM])
        if reachable:
            return AdjudicationResult(
                outcome=AdjudicationOutcome.INSUFFICIENT,
                agreed_game_id=dominant_game_id,
                reports_counted=agree,
                both_teams_represented=False,
                anomaly_flags=flag,
                detail=f"insufficient: {agree} one-sided for {winner}, awaiting the other side",
            )
        return AdjudicationResult(
            outcome=AdjudicationOutcome.DISPUTE,
            agreed_game_id=dominant_game_id,
            reports_counted=agree,
            both_teams_represented=False,
            anomaly_flags=flag,
            detail=f"dispute: one-sided quorum {agree} for {winner}, all reports in",
        )

    # 5. Sub-quorum: benign capture race while reachable, else an all-in stalemate.
    if reachable:
        return AdjudicationResult(
            outcome=AdjudicationOutcome.INSUFFICIENT,
            agreed_game_id=dominant_game_id,
            reports_counted=agree,
            both_teams_represented=both_teams,
            anomaly_flags=codes,
            detail=f"insufficient: {agree}/{quorum_threshold} agreeing, capture race (reachable)",
        )
    return AdjudicationResult(
        outcome=AdjudicationOutcome.DISPUTE,
        agreed_game_id=dominant_game_id,
        reports_counted=agree,
        both_teams_represented=both_teams,
        anomaly_flags=codes,
        detail=f"dispute: all reports in but no quorum ({agree}/{quorum_threshold}), unresolvable",
    )
