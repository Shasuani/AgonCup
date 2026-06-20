"""Result quorum / anomaly adjudication (PLAN.md §3.6.6).

Phase-0 STUB. The full corroboration pipeline lands in Phase 6; this module
fixes the function signature and the decision contract so dependent slices can
build against it now.

Trust model (documented threat model from PLAN.md §3.6.6 / §9): every
``ResultReportDTO`` is an unsigned, user-attestable JSON blob captured over a
self-signed local LCU cert with user-controlled credentials — **fully forgeable
and adversarial**. No local source is a trust anchor. Integrity rests on
cross-checking up to 10 independent per-participant reports keyed by a pre-bound
``agreed_game_id`` with **both teams represented**, plus an anomaly pipeline and
bounded admin override.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from lol_schemas import ResultReportDTO

# The both-teams-represented quorum floor (PLAN.md §3.6.6 mitigation 1): a result
# auto-accepts only when at least this many distinct identity-bound reports agree
# on the same agreed_game_id and winning team AND >=1 agreeing report comes from
# each registered roster. Modest floor (not 8/10) to tolerate ~4 honest-missing
# reports (capture race) without defeating autonomy.
QUORUM_THRESHOLD = 6
TOTAL_PARTICIPANTS = 10


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


def adjudicate_result(
    reports: Sequence[ResultReportDTO],
    roster_by_team: Mapping[UUID, frozenset[str]],
    *,
    quorum_threshold: int = QUORUM_THRESHOLD,
) -> AdjudicationResult:
    """Adjudicate a match result from up to 10 forgeable participant reports.

    This is the Phase-0 STUB: it pins the signature and the accept/dispute/
    insufficient contract. The full implementation (Phase 6) will, per
    PLAN.md §3.6.6:

    1. Authenticate freshness: reject reports with missing/expired/reused or
       wrong-PUUID nonces and de-duplicate on ``(lcu_game_id, reporter_puuid,
       source)`` before counting.
    2. Require **unanimous game-id agreement** among reporters (reject splits
       across >1 game id, or a game id used by any prior match).
    3. Group surviving reports by ``(agreed_game_id, winner)`` and check the
       **>=6/10 with both rosters represented** quorum: at least one agreeing
       report's ``reporter_puuid`` must belong to *each* team in
       ``roster_by_team``. A one-sided majority can never auto-resolve.
    4. Cross-correlate the ``:2999`` kill streams and run the anomaly pipeline
       (asymmetric winner claims, KDA-vs-kill-stream contradiction, byte-identical
       payloads, schema-version disagreement, ...). Any high-severity anomaly ->
       ``DISPUTE``.
    5. Map the composite verdict to :class:`AdjudicationOutcome`:
       quorum met & clean -> ``ACCEPT``; adversarial conflict/anomaly ->
       ``DISPUTE``; benign shortfall -> ``INSUFFICIENT``. Admin override is the
       final escape hatch on every branch.

    Args:
        reports: the (up to 10) identity-bound, forgeable participant reports for
            a single match.
        roster_by_team: registered PUUIDs per team id; used to enforce the
            both-teams-represented rule. Exactly two entries for a 1v1 match.
        quorum_threshold: agreeing-report floor (default 6).

    Returns:
        An :class:`AdjudicationResult` carrying the outcome and audit detail.

    Raises:
        NotImplementedError: always — this is a Phase-0 stub.
    """

    raise NotImplementedError(
        "adjudicate_result is a Phase-0 stub; the quorum/anomaly pipeline lands in Phase 6 "
        f"(>= {quorum_threshold}/{TOTAL_PARTICIPANTS} agreeing reports, both teams represented)."
    )
