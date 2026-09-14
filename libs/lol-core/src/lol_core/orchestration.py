"""Pure autonomous-lobby orchestration state machine (PLAN.md §3.6.1).

This is the *brain* of the autonomous LCU match-operation loop. Both the backend
service and the arq worker watchdog consume these functions; keeping the module
**pure** (no DB, no IO, no clock except values passed in) makes the phase-gated
host-failover logic — the part that is genuinely hard to get right — exhaustively
unit-testable.

The module models PLAN.md §3.6.1 in three independent, composable pieces:

1. :func:`seed_host_succession` — the **deterministic, server-seeded** host
   succession order, bound to the roster and *never* attacker-steerable
   "first-checked-in". Only ``host_opt_in`` players are eligible; non-opted-in
   players are skipped, never commandeered.
2. :func:`decide_transition` — the phase-gated happy-path transition table
   ``ASSIGNED_HOST -> AWAITING_CREATE -> LOBBY_OPEN -> FILLING -> READY ->
   STARTING -> IN_GAME -> RESULT_PENDING`` plus the terminal
   ``ENDED``/``ABORTED``/``HOST_LOST`` edges, rejecting illegal jumps.
3. :func:`decide_failover` — the per-phase host-drop decision: promote-first if
   the host is still connected, else re-create at a *new epoch*; in
   ``STARTING`` confirm gameflow has not advanced before re-creating; ``IN_GAME``
   failover is a hard NO-OP; bounded epochs fall back to the manual lobby UI.

Fencing (PLAN.md §3.6.1): ``host_epoch`` is a **monotonic lease**. Stale-epoch
commands/acks are rejected by :func:`is_epoch_current` / :func:`reject_stale_ack`
so a deposed/flapping host cannot split-brain the lobby.

The module deals in opaque, hashable player identifiers (typically ``puuid``
strings) and imports neither a web framework nor a DB layer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from lol_schemas import LobbyPhase

__all__ = [
    # succession
    "RosterMember",
    "seed_host_succession",
    "next_successor",
    # transitions
    "HAPPY_PATH",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_PHASES",
    "PRE_GAME_PHASES",
    "LobbyEvent",
    "IllegalTransitionError",
    "decide_transition",
    # host-observed progress
    "lobby_phase_from_gameflow",
    "events_toward",
    # failover
    "FailoverAction",
    "Decision",
    "decide_failover",
    # epoch lease / fencing
    "is_epoch_current",
    "reject_stale_ack",
    # heartbeat
    "HeartbeatState",
    "apply_heartbeat",
    "heartbeat_is_stale",
]


# --------------------------------------------------------------------------- #
# Deterministic server-seeded host succession (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RosterMember:
    """One roster member considered for host designation (PLAN.md §3.6.1).

    ``puuid`` is the stable, opaque player identifier the succession order is
    bound to. ``host_opt_in`` reflects the check-in-time host opt-in: only
    opted-in members are eligible to be designated host; opted-out members are
    *skipped*, never commandeered.
    """

    puuid: str
    host_opt_in: bool


def seed_host_succession(
    roster: Iterable[RosterMember],
    *,
    server_seed: str,
) -> tuple[str, ...]:
    """Compute the deterministic, server-seeded host succession order.

    PLAN.md §3.6.1: "The succession order is **deterministic and server-seeded,
    bound to the roster** (never attacker-steerable 'first checked-in'). Only
    players who gave the **host opt-in** at check-in are eligible; non-opted-in
    players are *skipped*, never commandeered."

    The order is a stable shuffle keyed by ``HMAC``-style hashing of
    ``server_seed || puuid`` (SHA-256), so it is:

    * **Deterministic** — the same ``(roster, server_seed)`` always yields the
      same order, which is exactly what lets an idle worker recompute the next
      successor without consulting another worker.
    * **Server-seeded / unsteerable** — a player cannot influence their position
      by checking in first (the order ignores arrival time entirely) and cannot
      cheaply grind their ``puuid`` against an *unknown* per-match ``server_seed``.
    * **Roster-bound** — only the supplied roster participates; opted-out members
      are filtered *before* ordering so the result is exactly the eligible
      successor chain, host first.

    Ties on the sort key (astronomically unlikely with SHA-256) break on
    ``puuid`` so the result is still total and reproducible.

    Args:
        roster: the match roster. Duplicate ``puuid`` values are de-duplicated
            (first occurrence wins) so the succession is a set of distinct
            players.
        server_seed: an opaque per-match secret/seed owned by the backend
            (e.g. ``match_id`` mixed with a server secret). Must be stable for
            the lifetime of the match for the order to be reproducible.

    Returns:
        The eligible host succession order as a tuple of ``puuid`` strings, host
        (index 0) first. Empty if no member opted in.
    """

    seen: set[str] = set()
    eligible: list[str] = []
    for member in roster:
        if not member.host_opt_in:
            continue
        if member.puuid in seen:
            continue
        seen.add(member.puuid)
        eligible.append(member.puuid)

    def sort_key(puuid: str) -> tuple[bytes, str]:
        digest = hashlib.sha256(f"{server_seed}\x00{puuid}".encode()).digest()
        return (digest, puuid)

    return tuple(sorted(eligible, key=sort_key))


def next_successor(
    succession: Sequence[str],
    *,
    current_host: str | None,
) -> str | None:
    """Return the next eligible host after ``current_host`` in ``succession``.

    PLAN.md §3.6.1: re-create "by the next opted-in eligible successor". The
    succession is the precomputed deterministic order; the next successor is the
    member immediately after the current host. If ``current_host`` is ``None``
    the first successor is returned (initial designation). Returns ``None`` when
    the chain is exhausted (no standby remaining) — which the caller maps to the
    manual fallback.
    """

    if current_host is None:
        return succession[0] if succession else None
    try:
        idx = list(succession).index(current_host)
    except ValueError:
        # Current host is not in the chain (e.g. already deposed/removed): the
        # first remaining successor is next.
        return succession[0] if succession else None
    nxt = idx + 1
    return succession[nxt] if nxt < len(succession) else None


# --------------------------------------------------------------------------- #
# Phase-gated transition table (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #

#: The ordered happy path (PLAN.md §3.6.1).
HAPPY_PATH: tuple[LobbyPhase, ...] = (
    LobbyPhase.ASSIGNED_HOST,
    LobbyPhase.AWAITING_CREATE,
    LobbyPhase.LOBBY_OPEN,
    LobbyPhase.FILLING,
    LobbyPhase.READY,
    LobbyPhase.STARTING,
    LobbyPhase.IN_GAME,
    LobbyPhase.RESULT_PENDING,
)

#: Terminal phases — no outgoing transitions.
TERMINAL_PHASES: frozenset[LobbyPhase] = frozenset(
    {LobbyPhase.ENDED, LobbyPhase.ABORTED, LobbyPhase.HOST_LOST}
)

#: Pre-game phases where a host drop is recoverable (promote / re-create).
#: ``STARTING`` is intentionally excluded — it has its own gameflow-guarded rule.
PRE_GAME_PHASES: frozenset[LobbyPhase] = frozenset(
    {
        LobbyPhase.AWAITING_CREATE,
        LobbyPhase.LOBBY_OPEN,
        LobbyPhase.FILLING,
        LobbyPhase.READY,
    }
)


class LobbyEvent(StrEnum):
    """Events that drive :func:`decide_transition` (PLAN.md §3.6.1).

    These are the *legitimate* phase-advancing signals, mostly sourced from
    ``GET /lol-gameflow/v1/gameflow-phase`` (RE) and host acks — never from the
    ambiguous lobby DELETE alone (which is advisory corroboration only).
    """

    HOST_DESIGNATED = "host_designated"  # ASSIGNED_HOST -> AWAITING_CREATE
    LOBBY_CREATED = "lobby_created"  # AWAITING_CREATE -> LOBBY_OPEN
    INVITES_SENT = "invites_sent"  # LOBBY_OPEN -> FILLING
    ALL_MEMBERS_PRESENT = "all_members_present"  # FILLING -> READY
    CHAMP_SELECT_STARTED = "champ_select_started"  # READY -> STARTING
    GAME_STARTED = "game_started"  # STARTING -> IN_GAME
    GAME_ENDED = "game_ended"  # IN_GAME -> RESULT_PENDING
    RESULT_FINALIZED = "result_finalized"  # RESULT_PENDING -> ENDED
    ABORT = "abort"  # any non-terminal -> ABORTED
    HOST_LOST_TERMINAL = "host_lost_terminal"  # pre-game exhaustion -> HOST_LOST


#: The phase-gated transition table: ``current -> {event: next}``. Any
#: ``(current, event)`` pair not present here is illegal and raises.
ALLOWED_TRANSITIONS: dict[LobbyPhase, dict[LobbyEvent, LobbyPhase]] = {
    LobbyPhase.ASSIGNED_HOST: {
        LobbyEvent.HOST_DESIGNATED: LobbyPhase.AWAITING_CREATE,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.AWAITING_CREATE: {
        LobbyEvent.LOBBY_CREATED: LobbyPhase.LOBBY_OPEN,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.LOBBY_OPEN: {
        LobbyEvent.INVITES_SENT: LobbyPhase.FILLING,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.FILLING: {
        LobbyEvent.ALL_MEMBERS_PRESENT: LobbyPhase.READY,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.READY: {
        LobbyEvent.CHAMP_SELECT_STARTED: LobbyPhase.STARTING,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.STARTING: {
        LobbyEvent.GAME_STARTED: LobbyPhase.IN_GAME,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
        LobbyEvent.HOST_LOST_TERMINAL: LobbyPhase.HOST_LOST,
    },
    LobbyPhase.IN_GAME: {
        # Once the game has launched it survives any client death; the only exits
        # are EOG capture (-> RESULT_PENDING). No ABORT: the server-side match
        # cannot be torn down by us.
        LobbyEvent.GAME_ENDED: LobbyPhase.RESULT_PENDING,
    },
    LobbyPhase.RESULT_PENDING: {
        LobbyEvent.RESULT_FINALIZED: LobbyPhase.ENDED,
        LobbyEvent.ABORT: LobbyPhase.ABORTED,
    },
}


class IllegalTransitionError(ValueError):
    """Raised when ``decide_transition`` is given a non-permitted edge.

    Carries the offending ``current`` phase and ``event`` for audit logging.
    """

    def __init__(self, current: LobbyPhase, event: LobbyEvent) -> None:
        self.current = current
        self.event = event
        super().__init__(f"illegal lobby transition: {current.value} -[{event.value}]-> (none)")


def decide_transition(current: LobbyPhase, event: LobbyEvent) -> LobbyPhase:
    """Return the next phase for ``(current, event)`` or raise on an illegal edge.

    Pure, total over the legal edges of the phase-gated machine (PLAN.md §3.6.1).
    Terminal phases (:data:`TERMINAL_PHASES`) have no outgoing edges; any event
    from them — or any non-permitted ``(current, event)`` pair — raises
    :class:`IllegalTransitionError`, never silently no-ops, so an out-of-order
    gameflow signal surfaces as an anomaly rather than corrupting state.

    Args:
        current: the lobby's current phase.
        event: the legitimate driving event (gameflow signal / host ack).

    Returns:
        The resulting :class:`LobbyPhase`.

    Raises:
        IllegalTransitionError: if no edge is defined for ``(current, event)``.
    """

    edges = ALLOWED_TRANSITIONS.get(current, {})
    nxt = edges.get(event)
    if nxt is None:
        raise IllegalTransitionError(current, event)
    return nxt


# --------------------------------------------------------------------------- #
# Host-observed progress: gameflow phase -> lobby phase (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #

#: The event that ENTERS each happy-path phase — the edge the walk takes to reach it.
_ENTERING_EVENT: dict[LobbyPhase, LobbyEvent] = {
    LobbyPhase.AWAITING_CREATE: LobbyEvent.HOST_DESIGNATED,
    LobbyPhase.LOBBY_OPEN: LobbyEvent.LOBBY_CREATED,
    LobbyPhase.FILLING: LobbyEvent.INVITES_SENT,
    LobbyPhase.READY: LobbyEvent.ALL_MEMBERS_PRESENT,
    LobbyPhase.STARTING: LobbyEvent.CHAMP_SELECT_STARTED,
    LobbyPhase.IN_GAME: LobbyEvent.GAME_STARTED,
    LobbyPhase.RESULT_PENDING: LobbyEvent.GAME_ENDED,
}

#: Raw LCU gameflow phases (`RE`) that prove the game itself is underway or over.
#: Deliberately capped at ``IN_GAME``: closing the game (``GAME_ENDED``) belongs to
#: the result report, which is what actually carries the outcome.
_IN_GAME_GAMEFLOW: frozenset[str] = frozenset(
    {"GameStart", "InProgress", "Reconnect", "WaitingForStats", "PreEndOfGame", "EndOfGame"}
)


def lobby_phase_from_gameflow(
    gameflow_phase: str | None, *, members_present: int, full_lobby: int
) -> LobbyPhase | None:
    """The lobby phase the HOST's own League client proves has been reached.

    Nothing else in the backend ever fired ``INVITES_SENT`` .. ``GAME_STARTED``:
    the host ack moved a lobby to ``LOBBY_OPEN`` and it stayed there through
    champion select, the game and its result. The host already reports the raw
    gameflow phase on every heartbeat, and (while in the lobby) who is in it, so
    this reads that evidence:

    * ``ChampSelect`` -> ``STARTING``; a running / finished game -> ``IN_GAME``.
    * ``Lobby`` with every one of ``full_lobby`` roster players present ->
      ``READY``; with at least one roster player besides the host -> ``FILLING``.

    ``members_present`` counts ROSTER players only (the caller intersects the
    lobby with the match roster), so a stranger holding the password cannot make
    a lobby look full. Returns ``None`` when the evidence proves nothing.
    """
    if gameflow_phase == "ChampSelect":
        return LobbyPhase.STARTING
    if gameflow_phase in _IN_GAME_GAMEFLOW:
        return LobbyPhase.IN_GAME
    if gameflow_phase == "Lobby":
        if full_lobby > 0 and members_present >= full_lobby:
            return LobbyPhase.READY
        if members_present >= 2:
            return LobbyPhase.FILLING
    return None


def events_toward(current: LobbyPhase, target: LobbyPhase) -> tuple[LobbyEvent, ...]:
    """The happy-path events that walk ``current`` forward to ``target``.

    Evidence can skip steps — a host whose client first reports ``ChampSelect``
    proves the lobby filled even though nobody saw it fill — so the walk applies
    every intermediate edge in order (each still checked by
    :func:`decide_transition`). Forward only: an equal or earlier ``target`` (a
    dodge back to the lobby, a player leaving) and any phase off the happy path
    (terminal, ``HOST_LOST``) yield no events, never a step backwards.
    """
    if current not in HAPPY_PATH or target not in HAPPY_PATH:
        return ()
    start, end = HAPPY_PATH.index(current), HAPPY_PATH.index(target)
    return tuple(_ENTERING_EVENT[phase] for phase in HAPPY_PATH[start + 1 : end + 1])


# --------------------------------------------------------------------------- #
# Per-phase host-drop failover decision (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


class FailoverAction(StrEnum):
    """The action a host-drop decision resolves to (PLAN.md §3.6.1).

    * ``PROMOTE`` — lossless host promotion: the host is still connected, so hand
      the lobby to it under a new epoch without tearing it down. The only
      lossless path; preferred whenever available.
    * ``RECREATE`` — full re-create by the next opted-in successor at a *new
      epoch* + re-invite all members (host gone, but standby remains).
    * ``NOOP_INGAME`` — hard no-op: the game has launched server-side and survives
      any client death; the lobby no longer exists. Results fall to corroboration.
    * ``ABORT_TO_MANUAL`` — bounded epochs exhausted (or no opted-in standby
      left): stop all LCU automation and fall back to the manual lobby
      name + password UI.
    """

    PROMOTE = "promote"
    RECREATE = "recreate"
    NOOP_INGAME = "noop_ingame"
    ABORT_TO_MANUAL = "abort_to_manual"


@dataclass(frozen=True)
class Decision:
    """The resolved host-drop decision (PLAN.md §3.6.1).

    ``new_epoch`` is set only for ``PROMOTE`` / ``RECREATE`` (the lease bumps on
    every re-designation so stale-epoch commands from the deposed host are
    fenced out); it is ``None`` for ``NOOP_INGAME`` and ``ABORT_TO_MANUAL``.
    ``successor_puuid`` is the player the lobby moves to (``PROMOTE`` keeps the
    current host; ``RECREATE`` advances to the next successor); ``None`` for the
    no-op / manual branches. ``reason`` is a short audit string for the
    append-only AuditLog event PLAN.md §3.6.1 mandates on every failover.
    """

    action: FailoverAction
    new_epoch: int | None = None
    successor_puuid: str | None = None
    reason: str = ""


def decide_failover(
    state: LobbyPhase,
    *,
    host_connected: bool,
    phase: LobbyPhase | None = None,
    missed_heartbeats: int,
    grace: int,
    epochs_used: int,
    max_epochs: int,
    current_epoch: int,
    succession: Sequence[str] = (),
    current_host: str | None = None,
    gameflow_advanced: bool = False,
) -> Decision:
    """Decide what to do when a host appears to have dropped (PLAN.md §3.6.1).

    Pure encoding of the §3.6.1 per-phase host-drop rules. The watchdog supplies
    the observed liveness/phase signals; this function maps them to a
    :class:`Decision` with **no IO**.

    Decision order:

    1. **Grace gate.** Failover only triggers after a bounded grace window of
       missed heartbeats (``missed_heartbeats >= grace``) *and* only if the phase
       has not legitimately advanced (``phase``, when given, must still equal
       ``state``). When the host is healthy, still inside grace, or the phase
       advanced, the result is the benign ``NOOP_INGAME`` "do nothing" sentinel
       (with a distinguishing ``reason``); the caller acts only on
       ``PROMOTE``/``RECREATE``/``ABORT_TO_MANUAL``.

    2. **IN_GAME is a hard NO-OP** (PLAN.md §3.6.1): the match runs server-side
       and survives any client death; the lobby is gone. Always ``NOOP_INGAME``,
       regardless of heartbeats — *before* the grace gate, because there is
       nothing to recover.

    3. **STARTING (champ-select):** re-create at a new epoch — *but first confirm
       gameflow has not advanced to GameStart/InProgress on any surviving
       client*. If ``gameflow_advanced`` is ``True`` the game launched: do **not**
       re-create, no-op to IN_GAME (``NOOP_INGAME``). Otherwise treat like a
       pre-game drop (promote-first / re-create / manual).

    4. **Pre-game (AWAITING_CREATE / LOBBY_OPEN / FILLING / READY):**
       promote-first if the host is still connected (lossless); else re-create by
       the next opted-in successor at a new epoch. If epochs are exhausted
       (``epochs_used >= max_epochs``) or no successor remains, abort to the
       manual fallback.

    Args:
        state: the lobby's current phase (the persisted ``MatchLobby.state``).
        host_connected: whether the *current* host's client is still connected
            (e.g. a guest-forwarded DELETE arrived but the host still answers, or
            the host flapped). Enables the lossless promote path.
        phase: the freshest gameflow phase observed by the watchdog. When given
            and it no longer equals ``state``, the phase legitimately advanced
            and failover is suppressed (no-op). ``None`` means "treat as
            unchanged".
        missed_heartbeats: consecutive missed pre-game heartbeats.
        grace: the bounded missed-heartbeat grace window before failover fires.
        epochs_used: how many epochs (re-designations) have been consumed.
        max_epochs: the bounded cap (~2–3) before manual fallback.
        current_epoch: the lobby's current monotonic ``host_epoch`` lease; the
            new epoch is ``current_epoch + 1``.
        succession: the deterministic succession order (host first).
        current_host: the current host ``puuid`` (to locate the next successor).
        gameflow_advanced: STARTING-only guard — ``True`` iff some surviving
            client reports gameflow already at GameStart/InProgress.

    Returns:
        A :class:`Decision`. ``NOOP_INGAME`` is also reused for the benign
        "do nothing" cases (host healthy, still inside grace, or phase advanced),
        with a ``reason`` distinguishing them — the caller acts only on
        ``PROMOTE``/``RECREATE``/``ABORT_TO_MANUAL``.
    """

    new_epoch = current_epoch + 1

    # Rule 2: IN_GAME failover is a hard NO-OP, before any grace/heartbeat logic.
    if state == LobbyPhase.IN_GAME:
        return Decision(
            action=FailoverAction.NOOP_INGAME,
            reason="in_game: match runs server-side and survives client death; failover is a no-op",
        )

    # Terminal phases: nothing to recover.
    if state in TERMINAL_PHASES:
        return Decision(
            action=FailoverAction.NOOP_INGAME,
            reason=f"terminal phase {state.value}: no failover",
        )

    # Grace gate: failover only after the bounded missed-heartbeat window AND only
    # if the phase has not legitimately advanced.
    if phase is not None and phase != state:
        return Decision(
            action=FailoverAction.NOOP_INGAME,
            reason=f"phase advanced {state.value}->{phase.value}: legitimate, suppress failover",
        )
    if missed_heartbeats < grace:
        return Decision(
            action=FailoverAction.NOOP_INGAME,
            reason=f"within grace window ({missed_heartbeats}/{grace} missed): wait",
        )

    # Rule 3: STARTING (champ-select) — confirm gameflow has not advanced first.
    if state == LobbyPhase.STARTING and gameflow_advanced:
        return Decision(
            action=FailoverAction.NOOP_INGAME,
            reason="starting: gameflow advanced to GameStart/InProgress; launched, no recreate",
        )

    # Bounded-attempt exhaustion -> manual fallback (PLAN.md §3.6.1).
    if epochs_used >= max_epochs:
        return Decision(
            action=FailoverAction.ABORT_TO_MANUAL,
            reason=f"epochs exhausted ({epochs_used}/{max_epochs}): fall back to manual lobby UI",
        )

    # Pre-game (and non-advanced STARTING): promote-first if host still connected.
    if host_connected:
        return Decision(
            action=FailoverAction.PROMOTE,
            new_epoch=new_epoch,
            successor_puuid=current_host,
            reason="host still connected: lossless promote at new epoch",
        )

    # Host gone: re-create by the next opted-in successor at a new epoch.
    successor = next_successor(succession, current_host=current_host)
    if successor is None:
        return Decision(
            action=FailoverAction.ABORT_TO_MANUAL,
            reason="no opted-in standby successor remaining: fall back to manual lobby UI",
        )
    return Decision(
        action=FailoverAction.RECREATE,
        new_epoch=new_epoch,
        successor_puuid=successor,
        reason=f"host gone: re-create by successor at epoch {new_epoch} + re-invite",
    )


# --------------------------------------------------------------------------- #
# Epoch lease / split-brain fencing (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


def is_epoch_current(command_epoch: int, current_epoch: int) -> bool:
    """Whether a command/ack carrying ``command_epoch`` is still authoritative.

    PLAN.md §3.6.1: ``host_epoch`` is a **monotonic lease**; "the client refuses
    any command with a stale epoch". A command is current iff its epoch equals
    the lobby's current lease. A *lower* epoch is a deposed/flapping host
    (split-brain) and is rejected; a *higher* epoch is impossible against a
    monotonic backend-owned lease and is likewise rejected (fail-closed).
    """

    return command_epoch == current_epoch


def reject_stale_ack(ack_epoch: int, current_epoch: int) -> bool:
    """Whether an inbound :class:`LobbyAck`/heartbeat must be rejected as stale.

    Convenience inverse of :func:`is_epoch_current` for the ack/heartbeat path: a
    deposed host that keeps acking under its old epoch is a split-brain witness
    and its acks must not advance state. Returns ``True`` when the ack should be
    dropped.
    """

    return not is_epoch_current(ack_epoch, current_epoch)


# --------------------------------------------------------------------------- #
# Heartbeat freshness (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HeartbeatState:
    """Watchdog-side heartbeat accounting for one match (PLAN.md §3.6.1).

    Pure value object: ``last_seen_seconds`` is the monotonic timestamp (seconds)
    of the last accepted heartbeat, and ``missed`` is the running count of
    consecutive missed beats. The watchdog folds successive ticks through
    :func:`apply_heartbeat`; no wall clock is read inside the module.
    """

    last_seen_seconds: float
    missed: int = 0


def apply_heartbeat(
    prior: HeartbeatState,
    *,
    now_seconds: float,
    heartbeat_seconds: float | None,
    interval_seconds: float,
    accepted: bool = True,
) -> HeartbeatState:
    """Fold a watchdog tick into the heartbeat freshness state (PLAN.md §3.6.1).

    The host posts a heartbeat every ~2–3s while pre-game (``interval_seconds``).
    On each watchdog tick:

    * If a fresh, **accepted** heartbeat arrived (``heartbeat_seconds`` is not
      ``None`` and ``accepted`` — a stale-epoch heartbeat is rejected upstream via
      :func:`reject_stale_ack` and passed with ``accepted=False``), reset
      ``missed`` to 0 and advance ``last_seen_seconds``.
    * Otherwise, compute how many whole intervals have elapsed since the last
      accepted heartbeat and surface that as ``missed`` so the watchdog can
      compare against the grace window in :func:`decide_failover`.

    Args:
        prior: the previous :class:`HeartbeatState`.
        now_seconds: the current watchdog-tick time (monotonic seconds).
        heartbeat_seconds: timestamp of the heartbeat observed this tick, or
            ``None`` if none arrived.
        interval_seconds: the expected heartbeat interval (must be > 0).
        accepted: whether the observed heartbeat passed epoch fencing.

    Returns:
        The updated :class:`HeartbeatState`.

    Raises:
        ValueError: if ``interval_seconds`` is not positive.
    """

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    if heartbeat_seconds is not None and accepted:
        return HeartbeatState(last_seen_seconds=heartbeat_seconds, missed=0)

    elapsed = now_seconds - prior.last_seen_seconds
    missed = int(elapsed // interval_seconds) if elapsed > 0 else 0
    return HeartbeatState(last_seen_seconds=prior.last_seen_seconds, missed=missed)


def heartbeat_is_stale(
    state: HeartbeatState,
    *,
    grace: int,
) -> bool:
    """Whether the heartbeat has gone stale past the grace window (PLAN.md §3.6.1).

    Convenience predicate over :class:`HeartbeatState.missed` and the bounded
    grace window: ``True`` once at least ``grace`` consecutive beats are missed.
    """

    return state.missed >= grace


# Mapping kept for callers that want to validate the full table at import time.
_TRANSITION_VIEW: Mapping[LobbyPhase, Mapping[LobbyEvent, LobbyPhase]] = ALLOWED_TRANSITIONS
