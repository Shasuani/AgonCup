"""arq worker entrypoint (PLAN.md §1.1 / §3 / §7).

Run with::

    arq app.main.WorkerSettings

The worker tier owns the retriable / scheduled work that must not run on the web
event loop (PLAN.md §1.3): Account-V1 verification polling, the **per-match lobby
orchestration watchdog / host-failover state machine**, result corroboration /
anomaly scoring, stuck-match alerts, and backups.

Phase 0 status
--------------
This is a **runnable skeleton**. ``ping`` is a real, working job used by the
healthcheck and tests; the three v3 jobs (``lobby_watchdog``,
``score_corroboration``, ``account_verification_poll``) are **stubs** with
docstrings pinning their contracts to the PLAN. They register so the queue shape
is real, but raise ``NotImplementedError`` so nothing silently no-ops.

Trust note (PLAN.md §3.6.6 / §9): every result report this tier scores is an
unsigned, forgeable, adversarial blob. The worker never treats any single source
as authoritative; corroboration + anomaly scoring + admin override is the model.
"""

from __future__ import annotations

from typing import Any

from arq import cron  # noqa: F401  (re-exported for the commented cron example below)

from app.config import get_settings
from app.logging import configure_logging, get_logger

log = get_logger("worker")


# ---------------------------------------------------------------------------
# Worker lifecycle hooks
# ---------------------------------------------------------------------------
async def startup(ctx: dict[str, Any]) -> None:
    """arq startup hook — configure logging and stash shared resources in ``ctx``.

    Later phases will open the async SQLAlchemy engine here (with the same RLS
    GUC discipline as the backend, PLAN.md §7 Phase 1) and attach it to ``ctx``.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx["settings"] = settings
    log.info("worker.startup", environment=settings.environment)


async def shutdown(ctx: dict[str, Any]) -> None:
    """arq shutdown hook — dispose shared resources (DB engine, etc.)."""
    log.info("worker.shutdown")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
async def ping(ctx: dict[str, Any]) -> str:
    """Liveness job — enqueue/execute to prove the worker + broker are healthy.

    Used by the Docker ``HEALTHCHECK`` (round-trips a job through Redis) and by
    the unit tests. Returns the literal ``"pong"``.
    """
    log.info("job.ping", job_id=ctx.get("job_id"))
    return "pong"


async def lobby_watchdog(ctx: dict[str, Any], match_id: str) -> None:
    """Per-match lobby-orchestration heartbeat / host-failover state-machine tick.

    SKELETON (PLAN.md §3.6.1 — "Host designation + failover state machine,
    fenced, phase-gated"). The full implementation lands in Phase 2.

    One tick of the durable, retriable watchdog for a single ``MatchLobby``:

    * Read the host heartbeat (``last_host_heartbeat_at``) and the lobby
      ``state`` / phase. Phase comes from ``GET /lol-gameflow/v1/gameflow-phase``
      (RE) **not** the ambiguous lobby DELETE event (DELETE fires on both host
      crash *and* normal game launch; it is advisory corroboration only).
    * If the bounded grace window of missed heartbeats has elapsed **AND** the
      phase has not legitimately advanced, trigger failover:
        - ``AWAITING_CREATE/LOBBY_OPEN/FILLING/READY``: promote-first if the host
          is still connected (lossless), else full re-create by the next opted-in
          eligible successor at a **new ``host_epoch``** + re-invite all 9.
        - ``STARTING`` (champ-select): abort champ-select, re-create at a new
          epoch — but first confirm gameflow has not advanced to
          ``GameStart/InProgress`` on ANY surviving client.
        - ``IN_GAME`` (GameStart/InProgress/Reconnect): failover is a hard NO-OP
          (the game runs server-side and survives client death).
    * Re-designation is the single conditional update
      ``UPDATE ... WHERE match_id=? AND host_epoch=expected`` (single-flight
      optimistic concurrency / idempotency guard against double-create); 0 rows
      ⇒ another worker already handled it.
    * Bounded attempts (~2–3 epochs) → manual lobby name+password fallback.
      Every failover is a high-attention append-only AuditLog event.

    Args:
        ctx: arq job context (shared resources live here).
        match_id: the match whose lobby this watchdog tick services.

    Raises:
        NotImplementedError: always — Phase-0 stub (state machine lands Phase 2).
    """
    log.info("job.lobby_watchdog.stub", match_id=match_id)
    raise NotImplementedError(
        "lobby_watchdog is a Phase-0 stub; the fenced, phase-gated host-failover "
        "state machine lands in Phase 2 (PLAN.md §3.6.1)."
    )


async def score_corroboration(ctx: dict[str, Any], match_id: str) -> None:
    """Quorum + anomaly scoring for a match's result reports.

    SKELETON (PLAN.md §3.6.6 — "Result corroboration + admin-override integrity
    scheme"). Delegates the verdict to the shared, unit-tested adjudication logic
    in ``lol-core`` so backend and worker score results identically.

    The full job (Phase 6) will:

    * Load this match's (up to 10) identity-bound ``ResultReport`` rows and the
      registered ``roster_by_team`` mapping.
    * Call :func:`lol_core.adjudication.adjudicate_result` — which enforces the
      ``>=6/10`` agreeing reports with **both teams represented** quorum,
      unanimous-game-id binding, kill-stream cross-correlation, and the anomaly
      pipeline (asymmetric winner claims, byte-identical payloads, KDA-vs-kill
      contradictions, schema-version disagreement).
    * Map the :class:`~lol_core.adjudication.AdjudicationOutcome`:
      ``ACCEPT`` → mark ``MatchResult`` provisional + advance the bracket;
      ``DISPUTE`` → freeze + push to the anomaly-ranked admin override queue;
      ``INSUFFICIENT`` → re-request within the persistence window, do not advance.

    No source is authoritative; admin override is the final escape hatch on every
    branch (PLAN.md §9 trust boundary invariant).

    Args:
        ctx: arq job context.
        match_id: the match whose reports to corroborate.

    Raises:
        NotImplementedError: always — Phase-0 stub. The call into ``lol-core``'s
            ``adjudicate_result`` (itself a Phase-0 stub) is wired in Phase 6.
    """
    # Import here (not at module top) so the stub raise is the only behavior in
    # Phase 0 and lol-core stays an explicit, documented dependency edge.
    from lol_core.adjudication import adjudicate_result  # noqa: F401

    log.info("job.score_corroboration.stub", match_id=match_id)
    raise NotImplementedError(
        "score_corroboration is a Phase-0 stub; it will call "
        "lol_core.adjudication.adjudicate_result over the match's ResultReports "
        "in Phase 6 (PLAN.md §3.6.6)."
    )


async def account_verification_poll(ctx: dict[str, Any], attempt_id: str) -> None:
    """Account-V1 nonce / profile-icon verification poll for one attempt.

    SKELETON (PLAN.md §3.1 "Account verification (hardened, Account-V1 only)" /
    §3.5). The full job (Phase 1) will, for the given ``VerificationAttempt``:

    * Poll Summoner-V4 ``by-puuid`` (via the rate-limited ``lol-riot`` client,
      honoring 429 ``Retry-After``) and compare ``profileIconId`` against the
      attempt's random ``target_icon_id``.
    * On match within the TTL → mark the ``RiotAccount`` verified (``verified``,
      ``verified_at``) and consume the single-use nonce.
    * Otherwise re-enqueue with bounded retries until the attempt's hard
      ``expires_at`` (then mark expired). PUUID is the canonical identity key;
      the icon nonce only proves ownership of the already-resolved PUUID.

    The LCU ``current-summoner`` PUUID is an advisory secondary signal only —
    never the verification authority.

    Args:
        ctx: arq job context.
        attempt_id: the ``VerificationAttempt`` to poll.

    Raises:
        NotImplementedError: always — Phase-0 stub (verification lands Phase 1).
    """
    log.info("job.account_verification_poll.stub", attempt_id=attempt_id)
    raise NotImplementedError(
        "account_verification_poll is a Phase-0 stub; the Account-V1 nonce/icon "
        "poll lands in Phase 1 (PLAN.md §3.1)."
    )


# ---------------------------------------------------------------------------
# Worker settings — `arq app.main.WorkerSettings`
# ---------------------------------------------------------------------------
class WorkerSettings:
    """arq worker definition (functions, hooks, Redis connection).

    arq imports this class by dotted path (``arq app.main.WorkerSettings``) and
    reads its attributes; it is intentionally a plain class, not a dataclass.
    """

    functions = [
        ping,
        lobby_watchdog,
        score_corroboration,
        account_verification_poll,
    ]

    on_startup = startup
    on_shutdown = shutdown

    # Redis broker derived from REDIS_URL (12-factor; see app.config).
    redis_settings = get_settings().arq_redis_settings()

    # --- Scheduled jobs (cron) ------------------------------------------------
    # Reconciliation sweep example — periodic cross-check of orchestration state
    # vs. observed reality (stuck-match alerts, orphan-lobby reaping, capture-race
    # re-requests). Enable in Phase 2/3 once the jobs below are implemented.
    #
    # cron_jobs = [
    #     # Every 60s: scan for matches stuck in AWAITING_CREATE/LOBBY_OPEN past
    #     # the SLO threshold and alert (PLAN.md §3.3 alert set / §9 dead-lobby).
    #     cron(reconcile_stuck_matches, second={0}),
    #     # Nightly 03:17 UTC: Postgres backup trigger (PLAN.md §7 Phase 1).
    #     cron(run_backup, hour={3}, minute={17}),
    # ]
