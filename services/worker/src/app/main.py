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

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from arq import cron  # noqa: F401  (re-exported for the commented cron example below)

from app.config import get_settings
from app.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from lol_riot import RiotAccountClient

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


# ---------------------------------------------------------------------------
# Account-V1 verification poll — config, collaborators, decision logic
# (PLAN.md §3.1 "Account verification (hardened)" / §3.5 Account-V1)
# ---------------------------------------------------------------------------
#: ``verification_attempts.status`` values (mirror the table CHECK constraint
#: and ``app.accounts.service`` on the backend side).
_ATTEMPT_PENDING = "pending"
_ATTEMPT_VERIFIED = "verified"
_ATTEMPT_EXPIRED = "expired"

#: Delay before a mismatch re-poll (bounded by the attempt's hard ``expires_at``,
#: so retries are wall-clock-bounded, not just count-bounded — PLAN.md §3.1).
_REENQUEUE_DELAY_SECONDS = 15

#: Player platform region (``na1``/``euw1``/...) → Summoner-V4 platform host.
#: Summoner-V4 ``by-puuid`` is PLATFORM-routed (PLAN.md §3.5); the attempt's
#: account stores the platform region, so the poll routes by it.
_DEFAULT_PLATFORM = "na1"

#: A decision the pure (no-DB) evaluator can return.
PollDecision = Literal["verified", "mismatch"]


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Flat view of a ``VerificationAttempt`` joined to its ``RiotAccount``.

    Decouples the pure decision logic + the gateway from the ORM (the worker is
    a standalone service without the backend's models package, PLAN.md §4): the
    Postgres gateway hydrates this from raw SQL, tests construct it directly.
    """

    attempt_id: str
    puuid: str
    target_icon_id: int
    status: str
    expires_at: datetime
    region: str


class VerificationGateway(Protocol):
    """The DB + Riot collaborators the verification poll needs (injectable).

    Stored under ``ctx["verification_gateway"]``; tests provide a fake, the
    worker ``startup`` hook will provide the Postgres-backed one once it opens
    the engine (it currently does not, hence the lazy build + bare-ctx guard).
    """

    def riot_client(self) -> RiotAccountClient: ...

    async def load_attempt(self, attempt_id: str) -> AttemptRecord | None: ...

    async def mark_verified(
        self, attempt_id: str, puuid: str, *, verified_at: datetime
    ) -> None: ...

    async def mark_expired(self, attempt_id: str) -> None: ...

    async def reenqueue(self, attempt_id: str, *, delay_seconds: int) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _resolve_gateway(ctx: dict[str, Any]) -> VerificationGateway:
    """Return the verification gateway from ``ctx`` or build it from settings.

    A bare ``ctx`` (no injected gateway and no worker ``settings``) cannot run —
    the engine/Riot client are not wired yet — so this raises
    ``NotImplementedError`` (keeps the Phase-0 queue-shape smoke test honest).
    """
    gateway: VerificationGateway | None = ctx.get("verification_gateway")
    if gateway is not None:
        return gateway
    if "settings" not in ctx:
        raise NotImplementedError(
            "account_verification_poll needs a verification gateway (DB engine + "
            "Riot client) in ctx; the worker startup hook wires it in Phase 1 "
            "(PLAN.md §3.1). Inject ctx['verification_gateway'] to run."
        )
    gateway = _PostgresVerificationGateway.from_settings(ctx["settings"])
    ctx["verification_gateway"] = gateway
    return gateway


async def _evaluate_attempt(
    record: AttemptRecord, client: RiotAccountClient, now: datetime
) -> PollDecision:
    """Poll Summoner-V4 once and decide verified / mismatch / expired.

    Pure-ish: given the attempt record + a Riot client, returns the decision
    WITHOUT touching the DB. ``verified`` only when the live ``profileIconId``
    equals the attempt's random ``target_icon_id`` (PLAN.md §3.1). A 404 (icon /
    account unreadable) and any transient Riot error degrade to ``mismatch`` so
    the bounded re-enqueue path retries until the hard TTL — never a hard fail.
    """
    from lol_riot import NotFound, PlatformRoute, RateLimited, RiotApiError

    try:
        platform = PlatformRoute(record.region.lower())
    except ValueError:
        platform = PlatformRoute(_DEFAULT_PLATFORM)

    try:
        summoner = await client.get_summoner_by_puuid(record.puuid, platform)
    except NotFound:
        return "mismatch"
    except (RateLimited, RiotApiError) as exc:
        log.warning(
            "job.account_verification_poll.riot_error",
            attempt_id=record.attempt_id,
            error=str(exc),
        )
        return "mismatch"

    if summoner.profile_icon_id == record.target_icon_id:
        return "verified"
    return "mismatch"


async def account_verification_poll(ctx: dict[str, Any], attempt_id: str) -> str:
    """Account-V1 nonce / profile-icon verification poll for one attempt.

    PLAN.md §3.1 ("Account verification, hardened, Account-V1 only") / §3.5. For
    the given ``VerificationAttempt`` this:

    * Loads the attempt + its ``RiotAccount``; short-circuits if the attempt is
      no longer ``pending`` (idempotent — another poll already resolved it).
    * If past the hard ``expires_at`` TTL, marks the attempt ``expired`` and
      stops (bounded by the wall clock, not just retry count).
    * Otherwise polls Summoner-V4 ``by-puuid`` via the rate-limited ``lol-riot``
      client (honoring 429 ``Retry-After``) and compares ``profileIconId``
      against the attempt's random ``target_icon_id``:
        - **match** → mark the ``RiotAccount`` verified (``verified=True`` +
          ``verified_at``), consume the single-use nonce (status ``verified``).
        - **mismatch** → re-enqueue with a bounded delay until ``expires_at``.

    PUUID is the canonical identity key (PLAN.md §2); the icon nonce only proves
    ownership of the already-resolved PUUID. The LCU ``current-summoner`` PUUID
    is an advisory secondary signal only — never the verification authority.

    The DB + Riot collaborators are taken from ``ctx`` (a
    :class:`VerificationGateway` under ``ctx["verification_gateway"]`` and the
    enqueue/now hooks); when absent the gateway is built lazily from the worker
    settings. A bare ``ctx`` (no settings) cannot run and raises
    ``NotImplementedError`` so the Phase-0 queue-shape smoke test still holds
    until the worker ``startup`` hook wires the engine + Riot client.

    Returns:
        A terminal/transition status string (``"verified"`` | ``"expired"`` |
        ``"mismatch"`` | ``"already_resolved"``) — handy for tests + logging.
    """
    gateway = _resolve_gateway(ctx)
    now = ctx.get("now_fn", _utcnow)()

    record = await gateway.load_attempt(attempt_id)
    if record is None:
        log.warning("job.account_verification_poll.missing", attempt_id=attempt_id)
        return "missing"

    if record.status != _ATTEMPT_PENDING:
        log.info(
            "job.account_verification_poll.already_resolved",
            attempt_id=attempt_id,
            status=record.status,
        )
        return "already_resolved"

    if now >= record.expires_at:
        await gateway.mark_expired(attempt_id)
        log.info("job.account_verification_poll.expired", attempt_id=attempt_id)
        return "expired"

    decision = await _evaluate_attempt(record, gateway.riot_client(), now)

    if decision == "verified":
        await gateway.mark_verified(attempt_id, record.puuid, verified_at=now)
        log.info(
            "job.account_verification_poll.verified",
            attempt_id=attempt_id,
            puuid=record.puuid,
        )
        return "verified"

    # Mismatch (or a transient Riot error degraded to a miss): re-enqueue until
    # the hard TTL (PLAN.md §3.1 bounded retries).
    await gateway.reenqueue(attempt_id, delay_seconds=_REENQUEUE_DELAY_SECONDS)
    log.info(
        "job.account_verification_poll.mismatch",
        attempt_id=attempt_id,
        target_icon_id=record.target_icon_id,
    )
    return "mismatch"


# ---------------------------------------------------------------------------
# Default Postgres + Riot verification gateway (PLAN.md §3.1 / §4)
# ---------------------------------------------------------------------------
# SQL kept as module constants so the (otherwise model-free) worker reads/writes
# the same ``verification_attempts`` / ``riot_accounts`` rows the backend ORM
# owns. ``RiotAccount`` / ``VerificationAttempt`` are GLOBAL (not guild-scoped,
# PLAN.md §2), so NO ``app.current_guild`` GUC is set here.
_SQL_LOAD_ATTEMPT = """
    SELECT va.id::text AS attempt_id,
           va.puuid,
           va.target_icon_id,
           va.status,
           va.expires_at,
           COALESCE(ra.region, :default_region) AS region
    FROM verification_attempts va
    LEFT JOIN riot_accounts ra ON ra.puuid = va.puuid
    WHERE va.id = :attempt_id
"""
_SQL_MARK_EXPIRED = """
    UPDATE verification_attempts
    SET status = :expired
    WHERE id = :attempt_id AND status = :pending
"""
_SQL_MARK_ATTEMPT_VERIFIED = """
    UPDATE verification_attempts
    SET status = :verified
    WHERE id = :attempt_id AND status = :pending
"""
_SQL_MARK_ACCOUNT_VERIFIED = """
    UPDATE riot_accounts
    SET verified = true, verified_at = :verified_at
    WHERE puuid = :puuid
"""


class _PostgresVerificationGateway:
    """Postgres + Riot implementation of :class:`VerificationGateway`.

    Built lazily from the worker settings (the ``startup`` hook does not yet open
    an engine). Holds its own async engine + a shared rate-limited
    :class:`RiotAccountClient` and re-enqueues via an arq pool. All resources are
    created on first use and reused for the worker's lifetime.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._engine: Any = None
        self._riot: RiotAccountClient | None = None
        self._pool: Any = None

    @classmethod
    def from_settings(cls, settings: Any) -> _PostgresVerificationGateway:
        return cls(settings)

    def _get_engine(self) -> Any:
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine

            self._engine = create_async_engine(
                self._settings.database_url, pool_pre_ping=True, future=True
            )
        return self._engine

    def riot_client(self) -> RiotAccountClient:
        if self._riot is None:
            from lol_riot import ContinentalRoute, RiotAccountClient

            api_key = getattr(self._settings, "riot_api_key", "")
            region = getattr(self._settings, "riot_region", "americas")
            try:
                route = ContinentalRoute(str(region).lower())
            except ValueError:
                route = ContinentalRoute.AMERICAS
            self._riot = RiotAccountClient(api_key, route)
        return self._riot

    async def load_attempt(self, attempt_id: str) -> AttemptRecord | None:
        from sqlalchemy import text

        async with self._get_engine().connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(_SQL_LOAD_ATTEMPT),
                        {"attempt_id": attempt_id, "default_region": _DEFAULT_PLATFORM},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return AttemptRecord(
            attempt_id=row["attempt_id"],
            puuid=row["puuid"],
            target_icon_id=int(row["target_icon_id"]),
            status=row["status"],
            expires_at=row["expires_at"],
            region=row["region"],
        )

    async def mark_verified(self, attempt_id: str, puuid: str, *, verified_at: datetime) -> None:
        from sqlalchemy import text

        async with self._get_engine().begin() as conn:
            await conn.execute(
                text(_SQL_MARK_ATTEMPT_VERIFIED),
                {
                    "attempt_id": attempt_id,
                    "verified": _ATTEMPT_VERIFIED,
                    "pending": _ATTEMPT_PENDING,
                },
            )
            await conn.execute(
                text(_SQL_MARK_ACCOUNT_VERIFIED),
                {"puuid": puuid, "verified_at": verified_at},
            )

    async def mark_expired(self, attempt_id: str) -> None:
        from sqlalchemy import text

        async with self._get_engine().begin() as conn:
            await conn.execute(
                text(_SQL_MARK_EXPIRED),
                {
                    "attempt_id": attempt_id,
                    "expired": _ATTEMPT_EXPIRED,
                    "pending": _ATTEMPT_PENDING,
                },
            )

    async def reenqueue(self, attempt_id: str, *, delay_seconds: int) -> None:
        from arq import create_pool

        if self._pool is None:
            self._pool = await create_pool(self._settings.arq_redis_settings())
        await self._pool.enqueue_job(
            "account_verification_poll", attempt_id, _defer_by=delay_seconds
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
