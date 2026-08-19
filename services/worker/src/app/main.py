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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from arq import cron  # noqa: F401  (re-exported for the commented cron example below)
from lol_core import Decision, FailoverAction, decide_failover
from lol_schemas import LobbyPhase

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


# ---------------------------------------------------------------------------
# Lobby watchdog — record view + injectable gateway (PLAN.md §3.6.1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LobbyRecord:
    """Flat view of a ``MatchLobby`` row the watchdog needs (PLAN.md §3.6.1).

    Decouples the pure decision flow + the gateway from the backend ORM (the
    worker imports ``lol_core`` only, never the backend app package, PLAN.md §4):
    the Postgres gateway hydrates this from raw SQL, tests construct it directly.

    ``host_connected`` and ``gameflow_advanced`` are the freshest
    observed-reality signals (e.g. derived from ``ClientSession`` liveness /
    forwarded gameflow phase) fed into :func:`lol_core.orchestration.decide_failover`;
    ``phase`` is the freshest gameflow phase (``None`` ⇒ "treat as unchanged",
    i.e. equal to ``state``).
    """

    match_id: str
    guild_id: str
    state: LobbyPhase
    host_puuid: str | None
    host_succession: tuple[str, ...]
    host_epoch: int
    last_host_heartbeat_at: datetime | None
    host_connected: bool = False
    gameflow_advanced: bool = False
    phase: LobbyPhase | None = None


class LobbyGateway(Protocol):
    """The DB collaborators the lobby watchdog needs (injectable).

    Stored under ``ctx["lobby_gateway"]``; tests provide a fake, the worker
    ``startup`` hook will provide the Postgres-backed one once it opens the engine
    (it currently does not, hence the lazy build + bare-ctx guard, mirroring
    :class:`VerificationGateway`).
    """

    async def load_lobby(self, match_id: str, *, guild_id: str | None = None) -> LobbyRecord | None:
        """Load one lobby. ``guild_id`` scopes the RLS GUC — REQUIRED under the
        app_rw role (``match_lobbies`` is FORCE-RLS; a GUC-less read sees zero
        rows); ``None`` only works on a BYPASSRLS/dev owner connection."""
        ...

    async def list_live_lobbies(self) -> list[tuple[str, str]]:
        """All (match_id, guild_id) pairs whose lobby is in a host-bearing state,
        across every active guild (the sweep's work list)."""
        ...

    async def apply_failover(
        self,
        *,
        match_id: str,
        guild_id: str,
        expected_epoch: int,
        new_epoch: int | None,
        new_host_puuid: str | None,
        new_state: LobbyPhase,
    ) -> int:
        """Fenced conditional UPDATE; return the number of rows updated (0 or 1)."""
        ...

    async def write_audit_log(
        self,
        *,
        guild_id: str,
        action: str,
        entity: str,
        before: dict[str, Any],
        after: dict[str, Any],
        ts: datetime,
    ) -> None: ...


async def lobby_watchdog(ctx: dict[str, Any], match_id: str, guild_id: str | None = None) -> str:
    """Per-match lobby-orchestration heartbeat / host-failover state-machine tick.

    PLAN.md §3.6.1 ("Host designation + failover state machine — fenced,
    phase-gated"). One tick of the durable, retriable watchdog for a single
    ``MatchLobby``. The hard part — the per-phase host-drop rules — is **not**
    reimplemented here: it is delegated wholesale to the pure, exhaustively
    unit-tested :func:`lol_core.orchestration.decide_failover`. This job is the
    durable shell around that decision:

    1. **Load** the ``MatchLobby`` row (state, ``host_puuid``, ``host_epoch``,
       ``host_succession``, ``last_host_heartbeat_at``) for ``match_id``.
    2. **Compute** the observed liveness signals: how many ~2–3s heartbeats have
       been missed since ``last_host_heartbeat_at`` (folded through the injected
       clock so tests stay deterministic), the current phase, and how many epochs
       have already been consumed.
    3. **Decide** by calling ``decide_failover`` — the §3.6.1 rules:
       ``IN_GAME``/terminal → hard NO-OP; within grace / phase-advanced → benign
       NO-OP; pre-game (or non-advanced ``STARTING``) → promote-first if the host
       is still connected, else re-create by the next opted-in successor at a new
       epoch; epoch exhaustion / no standby → abort to the manual lobby UI.
    4. **Apply** the resulting ``PROMOTE`` / ``RECREATE`` / ``ABORT_TO_MANUAL``
       action via a **single conditional UPDATE fenced on (match_id,
       host_epoch)** (single-flight optimistic concurrency / idempotency guard
       against double-create); 0 rows updated ⇒ another worker already handled
       this epoch, so this tick stands down. ``NOOP_INGAME`` writes nothing.
    5. **Audit** every *acted* failover (PROMOTE / RECREATE / ABORT_TO_MANUAL) as
       a high-attention append-only ``AuditLog`` row (PLAN.md §3.6.1 mandates this
       on every failover).

    Epochs are bounded (~2–3, ``ctx["lobby_max_epochs"]``); on exhaustion the
    decision is ``ABORT_TO_MANUAL`` and the lobby transitions to ``HOST_LOST``,
    surfacing the manual lobby name+password fallback.

    The DB collaborator is taken from ``ctx`` (a :class:`LobbyGateway` under
    ``ctx["lobby_gateway"]``); when absent it is built lazily from the worker
    settings. A bare ``ctx`` (no settings) cannot run and raises
    ``NotImplementedError`` so the Phase-0 queue-shape smoke test still holds
    until the worker ``startup`` hook wires the engine.

    Args:
        ctx: arq job context (shared resources live here). Honors the optional
            tuning knobs ``lobby_heartbeat_grace`` (missed-beat window),
            ``lobby_heartbeat_interval_seconds``, ``lobby_max_epochs``, and the
            injected ``now_fn`` clock.
        match_id: the match whose lobby this watchdog tick services.

    Returns:
        A short status string for tests + logging: the resolved
        :class:`~lol_core.orchestration.FailoverAction` value (``promote`` /
        ``recreate`` / ``noop_ingame`` / ``abort_to_manual``), ``"missing"`` if
        the lobby row is gone, or ``"superseded"`` if the fenced UPDATE matched 0
        rows (another worker won the race).
    """
    gateway = _resolve_lobby_gateway(ctx)
    now = ctx.get("now_fn", _utcnow)()
    grace: int = ctx.get("lobby_heartbeat_grace", _HEARTBEAT_GRACE)
    interval: float = ctx.get("lobby_heartbeat_interval_seconds", _HEARTBEAT_INTERVAL_SECONDS)
    max_epochs: int = ctx.get("lobby_max_epochs", _MAX_FAILOVER_EPOCHS)

    record = await gateway.load_lobby(match_id, guild_id=guild_id)
    if record is None:
        log.warning("job.lobby_watchdog.missing", match_id=match_id)
        return "missing"

    missed = _missed_heartbeats(record.last_host_heartbeat_at, now=now, interval_seconds=interval)

    decision = decide_failover(
        record.state,
        host_connected=record.host_connected,
        phase=record.phase,
        missed_heartbeats=missed,
        grace=grace,
        epochs_used=record.host_epoch,
        max_epochs=max_epochs,
        current_epoch=record.host_epoch,
        succession=record.host_succession,
        current_host=record.host_puuid,
        gameflow_advanced=record.gameflow_advanced,
    )

    # NO-OP branches (IN_GAME, terminal, within-grace, phase-advanced): no write,
    # no audit. The caller acts ONLY on PROMOTE/RECREATE/ABORT_TO_MANUAL.
    if decision.action is FailoverAction.NOOP_INGAME:
        log.info(
            "job.lobby_watchdog.noop",
            match_id=match_id,
            state=record.state.value,
            missed_heartbeats=missed,
            reason=decision.reason,
        )
        return decision.action.value

    return await _apply_failover(gateway, record, decision, now=now)


async def lobby_watchdog_sweep(ctx: dict[str, Any]) -> str:
    """Cron sweep: run a watchdog tick over every live lobby (PLAN.md §3.6.1).

    The per-match :func:`lobby_watchdog` is the brain, but nothing ever enqueued
    it — failover only fired when a client happened to call the HTTP endpoint.
    This sweep is the missing driver: every 30s it lists the host-bearing lobbies
    across all active guilds and ticks each one. ``decide_failover`` no-ops
    inside the grace window, so the cadence is safe; one failing lobby is logged
    and skipped rather than aborting the sweep.
    """
    gateway = _resolve_lobby_gateway(ctx)
    lobbies = await gateway.list_live_lobbies()
    acted = 0
    for match_id, guild_id in lobbies:
        try:
            outcome = await lobby_watchdog(ctx, match_id, guild_id)
        except Exception:
            log.exception("job.lobby_watchdog_sweep.tick_failed", match_id=match_id)
            continue
        if outcome in ("promote", "recreate", "abort_to_manual"):
            acted += 1
    log.info("job.lobby_watchdog_sweep.done", swept=len(lobbies), acted=acted)
    return f"swept:{len(lobbies)} acted:{acted}"


# --- lobby watchdog: tuning knobs + helpers (PLAN.md §3.6.1) ---------------
#: Missed ~2–3s heartbeats tolerated before failover fires (bounded grace
#: window). Overridable via ``ctx["lobby_heartbeat_grace"]``.
_HEARTBEAT_GRACE = 3

#: Expected host heartbeat interval while pre-game (PLAN.md §3.6.1: ~2–3s).
_HEARTBEAT_INTERVAL_SECONDS = 3.0

#: Bounded failover/re-create cap (~2–3 epochs) before the manual lobby UI
#: fallback (PLAN.md §3.6.1 "bounded attempts"). ``host_epoch`` starts at 0, so a
#: cap of 3 allows epochs 0->1->2->3 before exhaustion.
_MAX_FAILOVER_EPOCHS = 3

#: AuditLog ``action`` for a failover event (high-attention, append-only).
_AUDIT_ACTION_FAILOVER = "lobby_failover"
#: AuditLog ``entity`` tag.
_AUDIT_ENTITY_LOBBY = "match_lobby"


def _missed_heartbeats(
    last_heartbeat_at: datetime | None,
    *,
    now: datetime,
    interval_seconds: float,
) -> int:
    """How many whole heartbeat intervals have been missed (PLAN.md §3.6.1).

    Folds the persisted ``last_host_heartbeat_at`` against the injected clock the
    same way :func:`lol_core.orchestration.apply_heartbeat` folds monotonic
    seconds — but over wall-clock ``datetime`` columns, which is what the row
    actually stores. A ``None`` heartbeat (host never beat) is treated as
    maximally stale so an un-created/abandoned lobby fails over rather than
    hanging forever. Never returns negative (a future-dated heartbeat under clock
    skew counts as 0 missed).
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if last_heartbeat_at is None:
        # No heartbeat ever observed: maximally stale (well past any grace window).
        return _MISSED_UNBOUNDED
    elapsed = (now - last_heartbeat_at).total_seconds()
    if elapsed <= 0:
        return 0
    return int(elapsed // interval_seconds)


#: A "never beat" lobby reports an effectively unbounded miss count so it always
#: clears any sane grace window (decide_failover then applies the phase rules).
_MISSED_UNBOUNDED = 1_000_000


async def _apply_failover(
    gateway: LobbyGateway,
    record: LobbyRecord,
    decision: Decision,
    *,
    now: datetime,
) -> str:
    """Apply an acted failover Decision via the fenced UPDATE + emit an AuditLog.

    PLAN.md §3.6.1: re-designation is the single conditional update
    ``UPDATE ... WHERE match_id=? AND host_epoch=expected`` — single-flight
    optimistic concurrency that fences out a deposed/flapping host and guards
    against a double-create across a failover. The fence epoch is the record's
    *current* ``host_epoch`` (the lease we observed); ``decision.new_epoch`` is
    the bumped lease we write (``None`` for ``ABORT_TO_MANUAL``, which moves to a
    terminal phase without bumping the lease).

    Returns the resolved :class:`FailoverAction` value, or ``"superseded"`` when
    the fenced UPDATE matched 0 rows (another worker already advanced the epoch).
    """
    new_state, new_host = _failover_target(record, decision)

    rows = await gateway.apply_failover(
        match_id=record.match_id,
        guild_id=record.guild_id,
        expected_epoch=record.host_epoch,
        new_epoch=decision.new_epoch,
        new_host_puuid=new_host,
        new_state=new_state,
    )
    if rows == 0:
        # 0 rows ⇒ another worker already handled this epoch (idempotency guard).
        log.info(
            "job.lobby_watchdog.superseded",
            match_id=record.match_id,
            expected_epoch=record.host_epoch,
            action=decision.action.value,
        )
        return "superseded"

    # Every acted failover is a high-attention append-only AuditLog event.
    await gateway.write_audit_log(
        guild_id=record.guild_id,
        action=_AUDIT_ACTION_FAILOVER,
        entity=_AUDIT_ENTITY_LOBBY,
        before={
            "match_id": record.match_id,
            "state": record.state.value,
            "host_epoch": record.host_epoch,
            "host_puuid": record.host_puuid,
        },
        after={
            "action": decision.action.value,
            "state": new_state.value,
            "host_epoch": decision.new_epoch,
            "host_puuid": new_host,
            "reason": decision.reason,
        },
        ts=now,
    )
    log.info(
        "job.lobby_watchdog.failover",
        match_id=record.match_id,
        action=decision.action.value,
        from_epoch=record.host_epoch,
        to_epoch=decision.new_epoch,
        new_state=new_state.value,
        reason=decision.reason,
    )
    return decision.action.value


def _failover_target(record: LobbyRecord, decision: Decision) -> tuple[LobbyPhase, str | None]:
    """Map a Decision to the ``(new_state, new_host_puuid)`` the UPDATE writes.

    * ``PROMOTE`` — lossless: keep the current phase, re-designate the (still
      connected) current host under the bumped epoch.
    * ``RECREATE`` — host gone: drive the lobby back to ``AWAITING_CREATE`` (the
      successor re-creates verbatim at the new epoch, PLAN.md §3.6.2) and set the
      new host to the next opted-in successor.
    * ``ABORT_TO_MANUAL`` — bounded epochs exhausted / no standby: terminal
      ``HOST_LOST`` phase, surfacing the manual lobby name+password fallback.
    """
    if decision.action is FailoverAction.RECREATE:
        return LobbyPhase.AWAITING_CREATE, decision.successor_puuid
    if decision.action is FailoverAction.PROMOTE:
        return record.state, decision.successor_puuid
    # ABORT_TO_MANUAL
    return LobbyPhase.HOST_LOST, record.host_puuid


def _resolve_lobby_gateway(ctx: dict[str, Any]) -> LobbyGateway:
    """Return the lobby gateway from ``ctx`` or build it from settings.

    Mirrors :func:`_resolve_gateway`: a bare ``ctx`` (no injected gateway and no
    worker ``settings``) cannot run — the engine is not wired yet — so this raises
    ``NotImplementedError`` (keeps the Phase-0 queue-shape smoke test honest).
    """
    gateway: LobbyGateway | None = ctx.get("lobby_gateway")
    if gateway is not None:
        return gateway
    if "settings" not in ctx:
        raise NotImplementedError(
            "lobby_watchdog needs a lobby gateway (DB engine) in ctx; the worker "
            "startup hook wires it in Phase 2 (PLAN.md §3.6.1). Inject "
            "ctx['lobby_gateway'] to run."
        )
    gateway = _PostgresLobbyGateway.from_settings(ctx["settings"])
    ctx["lobby_gateway"] = gateway
    return gateway


@dataclass(frozen=True)
class StalledMatch:
    """A match whose reports went quiet without reaching a verdict."""

    match_id: str
    guild_id: str
    tournament_id: str
    reports_counted: int


class CorroborationGateway(Protocol):
    """The DB collaborators the corroboration timeout sweep needs (injectable)."""

    async def list_stalled_matches(self, *, cutoff: datetime) -> list[StalledMatch]:
        """Matches with >=1 report, no MatchResult, newest report older than cutoff."""
        ...

    async def mark_disputed_timeout(self, match: StalledMatch, *, ts: datetime) -> int:
        """Freeze the stalled match as DISPUTED (+ audit); 0 rows if already decided."""
        ...


#: Quiet window after the LAST report before a stalemate escalates to the admin
#: dispute queue. Overridable via ``ctx["corroboration_timeout_seconds"]``.
_CORROBORATION_TIMEOUT_SECONDS = 600.0


async def score_corroboration(ctx: dict[str, Any]) -> str:
    """Cron sweep: escalate report stalemates to the admin dispute queue.

    The happy path is INLINE (PLAN.md §3.6.6): every WS report re-runs
    ``adjudicate_match`` in the backend, so a clean quorum decides itself the
    moment enough reports land. What the inline path cannot do is act when
    reports STOP arriving — with (say) 5 agreeing reports and 5 clients that
    never sent one, the verdict stays INSUFFICIENT forever, because the only
    trigger is the next report. This sweep is the missing timer: a match with at
    least one report, no decided/queued result, and nothing new for the timeout
    window is frozen as DISPUTED so an admin decides — mirroring the trust
    boundary (admin override is the escape hatch on every branch, PLAN.md §9).
    """
    gateway = _resolve_corroboration_gateway(ctx)
    now = ctx.get("now_fn", _utcnow)()
    timeout: float = ctx.get("corroboration_timeout_seconds", _CORROBORATION_TIMEOUT_SECONDS)
    cutoff = now - timedelta(seconds=timeout)

    stalled = await gateway.list_stalled_matches(cutoff=cutoff)
    escalated = 0
    for match in stalled:
        try:
            escalated += await gateway.mark_disputed_timeout(match, ts=now)
        except Exception:
            log.exception("job.score_corroboration.escalate_failed", match_id=match.match_id)
    if stalled:
        log.info("job.score_corroboration.done", stalled=len(stalled), escalated=escalated)
    return f"stalled:{len(stalled)} escalated:{escalated}"


def _resolve_corroboration_gateway(ctx: dict[str, Any]) -> CorroborationGateway:
    """ctx-injected gateway (tests), else build the Postgres one from settings.

    Mirrors :func:`_resolve_lobby_gateway`: a bare ``ctx`` cannot run.
    """
    gateway: CorroborationGateway | None = ctx.get("corroboration_gateway")
    if gateway is not None:
        return gateway
    if "settings" not in ctx:
        raise NotImplementedError(
            "score_corroboration needs a gateway (DB engine) in ctx; inject "
            "ctx['corroboration_gateway'] to run."
        )
    built = _PostgresCorroborationGateway.from_settings(ctx["settings"])
    ctx["corroboration_gateway"] = built
    return built


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
# Default Postgres lobby-watchdog gateway (PLAN.md §3.6.1 / §3.6.2 / §4)
# ---------------------------------------------------------------------------
# SQL kept as module constants so the (model-free) worker reads/writes the same
# ``match_lobbies`` / ``audit_log`` rows the backend ORM owns. ``MatchLobby`` is
# GUILD-SCOPED (PLAN.md §2), so the worker session MUST set ``app.current_guild``
# — the same RLS GUC discipline as the backend request path (PLAN.md §7 Phase 1 /
# §9 "RLS + pooled-connection leak"): ``SET LOCAL`` inside the transaction so the
# tenant context cannot bleed across pooled connections. The load is fenced by
# ``match_id``; the failover is the single conditional UPDATE fenced on
# ``(match_id, host_epoch)`` (single-flight optimistic concurrency, PLAN.md §3.6.1).
_SQL_LOAD_LOBBY = """
    SELECT match_id::text AS match_id,
           guild_id::text AS guild_id,
           state,
           host_puuid,
           host_succession,
           host_epoch,
           last_host_heartbeat_at
    FROM match_lobbies
    WHERE match_id = :match_id
"""
#: Host-bearing lobby states the sweep ticks. IN_GAME and later are excluded:
#: decide_failover hard-no-ops in-game, and post-game states have no host lease
#: worth policing — skipping them keeps the sweep query cheap.
_SQL_LIST_ACTIVE_GUILDS = """
    SELECT id::text FROM guilds WHERE status <> 'removed'
"""
_SQL_LIST_LIVE_LOBBIES = """
    SELECT match_id::text
    FROM match_lobbies
    WHERE state IN ('assigned_host', 'awaiting_create', 'lobby_open',
                    'filling', 'ready', 'starting')
"""
_SQL_APPLY_FAILOVER = """
    UPDATE match_lobbies
    SET host_epoch = COALESCE(:new_epoch, host_epoch),
        host_puuid = :new_host_puuid,
        state = :new_state
    WHERE match_id = :match_id
      AND host_epoch = :expected_epoch
      AND state NOT IN ('ended', 'aborted', 'host_lost')
"""
# The terminal-state guard is defense-in-depth: decide_failover already returns
# NOOP for terminal phases, but a stale enqueued tick whose observed epoch still
# matches must never resurrect a finished lobby (e.g. drive it back to
# AWAITING_CREATE). 0 rows updated on a terminal lobby reads as "superseded".
_SQL_INSERT_AUDIT = """
    INSERT INTO audit_log (actor_kind, guild_id, action, entity, before, after, ts)
    VALUES ('system', :guild_id, :action, :entity, :before, :after, :ts)
"""


class _PostgresLobbyGateway:
    """Postgres implementation of :class:`LobbyGateway` (PLAN.md §3.6.1).

    Built lazily from the worker settings (the ``startup`` hook does not yet open
    an engine). Connects as the same non-BYPASSRLS ``app_rw`` role the backend
    request path uses (``app_database_url`` / ``request_database_url``), so the
    FORCE-RLS guild policies bind; every guild-scoped statement runs inside a
    transaction that first sets ``app.current_guild`` via transaction-local
    ``set_config`` (PLAN.md §9). The engine is created on first use and reused for
    the worker's lifetime.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._engine: Any = None

    @classmethod
    def from_settings(cls, settings: Any) -> _PostgresLobbyGateway:
        return cls(settings)

    def _database_url(self) -> str:
        # Prefer the non-BYPASSRLS app_rw DSN (so RLS binds) when configured;
        # fall back to database_url for dev convenience (mirrors the backend's
        # ``request_database_url`` property, PLAN.md §9).
        app_url = getattr(self._settings, "app_database_url", "") or ""
        return app_url or self._settings.database_url

    def _get_engine(self) -> Any:
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine

            self._engine = create_async_engine(
                self._database_url(), pool_pre_ping=True, future=True
            )
        return self._engine

    async def load_lobby(self, match_id: str, *, guild_id: str | None = None) -> LobbyRecord | None:
        from sqlalchemy import text

        # match_lobbies is FORCE-RLS: under app_rw the read MUST run with the
        # tenant GUC set or it silently sees zero rows. A None guild_id is the
        # legacy/dev path (owner or BYPASSRLS connection only).
        async with self._get_engine().begin() as conn:
            if guild_id is not None:
                await conn.execute(
                    text("SELECT set_config('app.current_guild', :guild_id, true)"),
                    {"guild_id": guild_id},
                )
            row = (
                (await conn.execute(text(_SQL_LOAD_LOBBY), {"match_id": match_id}))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return LobbyRecord(
            match_id=row["match_id"],
            guild_id=row["guild_id"],
            state=LobbyPhase(row["state"]),
            host_puuid=row["host_puuid"],
            host_succession=tuple(row["host_succession"] or ()),
            host_epoch=int(row["host_epoch"]),
            last_host_heartbeat_at=row["last_host_heartbeat_at"],
        )

    async def apply_failover(
        self,
        *,
        match_id: str,
        guild_id: str,
        expected_epoch: int,
        new_epoch: int | None,
        new_host_puuid: str | None,
        new_state: LobbyPhase,
    ) -> int:
        from sqlalchemy import text

        # The fenced UPDATE touches a guild-scoped table, so set the tenant GUC
        # first — DIRECTLY from the loaded record's guild_id. The old sub-select
        # trick (resolve guild_id from match_lobbies inside the txn) was circular
        # under app_rw: that read itself ran GUC-less under RLS, returned NULL,
        # and the UPDATE matched nothing.
        async with self._get_engine().begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_guild', :guild_id, true)"),
                {"guild_id": guild_id},
            )
            result = await conn.execute(
                text(_SQL_APPLY_FAILOVER),
                {
                    "match_id": match_id,
                    "expected_epoch": expected_epoch,
                    "new_epoch": new_epoch,
                    "new_host_puuid": new_host_puuid,
                    "new_state": new_state.value,
                },
            )
        return result.rowcount or 0

    async def list_live_lobbies(self) -> list[tuple[str, str]]:
        from sqlalchemy import text

        # `guilds` is the global tenant root (not under guild RLS), so it is the
        # one table a GUC-less connection can enumerate; the per-guild lobby scan
        # then runs under that guild's own GUC so RLS binds normally.
        pairs: list[tuple[str, str]] = []
        async with self._get_engine().begin() as conn:
            guild_ids = [r[0] for r in (await conn.execute(text(_SQL_LIST_ACTIVE_GUILDS))).all()]
            for guild_id in guild_ids:
                await conn.execute(
                    text("SELECT set_config('app.current_guild', :guild_id, true)"),
                    {"guild_id": guild_id},
                )
                rows = (await conn.execute(text(_SQL_LIST_LIVE_LOBBIES))).all()
                pairs.extend((r[0], guild_id) for r in rows)
        return pairs

    async def write_audit_log(
        self,
        *,
        guild_id: str,
        action: str,
        entity: str,
        before: dict[str, Any],
        after: dict[str, Any],
        ts: datetime,
    ) -> None:
        import json

        from sqlalchemy import text

        async with self._get_engine().begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_guild', :guild_id, true)"),
                {"guild_id": guild_id},
            )
            await conn.execute(
                text(_SQL_INSERT_AUDIT),
                {
                    "guild_id": guild_id,
                    "action": action,
                    "entity": entity,
                    "before": json.dumps(before),
                    "after": json.dumps(after),
                    "ts": ts,
                },
            )


_SQL_LIST_STALLED_MATCHES = """
    SELECT m.id::text AS match_id,
           m.tournament_id::text AS tournament_id,
           count(DISTINCT r.reporter_puuid) AS reporters
    FROM matches m
    JOIN result_reports r ON r.match_id = m.id
    WHERE m.status <> 'completed'
      AND NOT EXISTS (SELECT 1 FROM match_results mr WHERE mr.match_id = m.id)
    GROUP BY m.id, m.tournament_id
    HAVING max(r.received_at) < :cutoff
"""
# id / state default server-side (gen_random_uuid() / 'disputed'); the NOT EXISTS
# guard makes the escalation idempotent against a concurrently-decided result.
_SQL_INSERT_TIMEOUT_DISPUTE = """
    INSERT INTO match_results
        (match_id, tournament_id, winner_team_id, decided_by, quorum_detail,
         state, guild_id, decided_at)
    SELECT :match_id, :tournament_id, NULL, 'quorum', CAST(:detail AS jsonb),
           'disputed', :guild_id, :ts
    WHERE NOT EXISTS (SELECT 1 FROM match_results WHERE match_id = :match_id)
"""


class _PostgresCorroborationGateway:
    """Postgres :class:`CorroborationGateway` (same engine pattern as the lobby one).

    Every statement runs with the tenant GUC set for the guild it touches, so
    RLS binds normally under the app_rw role; the cross-guild work list comes
    from the global ``guilds`` root, never from a GUC-less guild-scoped scan.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._engine: Any = None

    @classmethod
    def from_settings(cls, settings: Any) -> _PostgresCorroborationGateway:
        return cls(settings)

    def _database_url(self) -> str:
        app_url = getattr(self._settings, "app_database_url", "") or ""
        return app_url or self._settings.database_url

    def _get_engine(self) -> Any:
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine

            self._engine = create_async_engine(
                self._database_url(), pool_pre_ping=True, future=True
            )
        return self._engine

    async def list_stalled_matches(self, *, cutoff: datetime) -> list[StalledMatch]:
        from sqlalchemy import text

        stalled: list[StalledMatch] = []
        async with self._get_engine().begin() as conn:
            guild_ids = [r[0] for r in (await conn.execute(text(_SQL_LIST_ACTIVE_GUILDS))).all()]
            for guild_id in guild_ids:
                await conn.execute(
                    text("SELECT set_config('app.current_guild', :guild_id, true)"),
                    {"guild_id": guild_id},
                )
                rows = (
                    (await conn.execute(text(_SQL_LIST_STALLED_MATCHES), {"cutoff": cutoff}))
                    .mappings()
                    .all()
                )
                stalled.extend(
                    StalledMatch(
                        match_id=row["match_id"],
                        guild_id=guild_id,
                        tournament_id=row["tournament_id"],
                        reports_counted=int(row["reporters"]),
                    )
                    for row in rows
                )
        return stalled

    async def mark_disputed_timeout(self, match: StalledMatch, *, ts: datetime) -> int:
        import json

        from sqlalchemy import text

        detail = {
            "anomaly_flags": ["report_timeout"],
            "reports_counted": match.reports_counted,
            "both_teams_represented": False,
        }
        async with self._get_engine().begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_guild', :guild_id, true)"),
                {"guild_id": match.guild_id},
            )
            result = await conn.execute(
                text(_SQL_INSERT_TIMEOUT_DISPUTE),
                {
                    "match_id": match.match_id,
                    "tournament_id": match.tournament_id,
                    "guild_id": match.guild_id,
                    "detail": json.dumps(detail),
                    "ts": ts,
                },
            )
            rows = result.rowcount or 0
            if rows:
                await conn.execute(
                    text(_SQL_INSERT_AUDIT),
                    {
                        "guild_id": match.guild_id,
                        "action": "result.disputed",
                        "entity": "match",
                        "before": json.dumps({"match_id": match.match_id}),
                        "after": json.dumps(detail),
                        "ts": ts,
                    },
                )
        if rows:
            log.warning(
                "job.score_corroboration.timeout_dispute",
                match_id=match.match_id,
                reports_counted=match.reports_counted,
            )
        return rows


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
        lobby_watchdog_sweep,
        score_corroboration,
        account_verification_poll,
    ]

    on_startup = startup
    on_shutdown = shutdown

    # Redis broker derived from REDIS_URL (12-factor; see app.config).
    redis_settings = get_settings().arq_redis_settings()

    # --- Scheduled jobs (cron) ------------------------------------------------
    # The per-match jobs are the brains; these sweeps are the timers that drive
    # them — without a cron NOTHING enqueued the watchdog and failover only fired
    # when a client happened to call the HTTP endpoint.
    cron_jobs = [
        # Every 30s: tick the failover watchdog over every live lobby
        # (decide_failover no-ops inside the heartbeat grace window).
        cron(lobby_watchdog_sweep, second={0, 30}),
        # Every minute: escalate report stalemates to the admin dispute queue
        # (the inline adjudication only runs when a NEW report arrives).
        cron(score_corroboration, second={15}),
    ]
