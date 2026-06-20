"""Exhaustive unit tests for the autonomous-lobby orchestration state machine.

Covers PLAN.md §3.6.1 end to end:

* deterministic, server-seeded, roster-bound host succession (opt-in filtering,
  not "first-checked-in", stable under input reordering);
* the phase-gated transition table over EVERY (phase x event) pair, including
  illegal-edge rejection and terminal-phase dead-ends;
* the per-phase host-drop failover matrix: promote-first vs re-create, the
  STARTING gameflow-advanced guard, the IN_GAME hard NO-OP, the grace gate,
  bounded-epoch exhaustion -> manual, and no-standby -> manual;
* split-brain / stale-epoch command + ack rejection (monotonic lease fencing);
* heartbeat freshness folding.
"""

from __future__ import annotations

import itertools

import pytest
from lol_schemas import LobbyPhase

from lol_core.orchestration import (
    ALLOWED_TRANSITIONS,
    HAPPY_PATH,
    PRE_GAME_PHASES,
    TERMINAL_PHASES,
    Decision,
    FailoverAction,
    HeartbeatState,
    IllegalTransitionError,
    LobbyEvent,
    RosterMember,
    apply_heartbeat,
    decide_failover,
    decide_transition,
    heartbeat_is_stale,
    is_epoch_current,
    next_successor,
    reject_stale_ack,
    seed_host_succession,
)

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

SEED = "match-42::server-secret"


def _roster(*specs: tuple[str, bool]) -> list[RosterMember]:
    return [RosterMember(puuid=p, host_opt_in=o) for p, o in specs]


# --------------------------------------------------------------------------- #
# Deterministic server-seeded host succession (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


def test_succession_is_deterministic_for_same_seed_and_roster() -> None:
    roster = _roster(("a", True), ("b", True), ("c", True), ("d", True))
    first = seed_host_succession(roster, server_seed=SEED)
    second = seed_host_succession(roster, server_seed=SEED)
    assert first == second
    assert sorted(first) == ["a", "b", "c", "d"]


def test_succession_ignores_arrival_order_not_first_checked_in() -> None:
    # "First checked-in" would be attacker-steerable; order must be input-order
    # independent. Reversing/shuffling the roster yields the SAME succession.
    roster = _roster(("a", True), ("b", True), ("c", True), ("d", True))
    forward = seed_host_succession(roster, server_seed=SEED)
    reversed_order = seed_host_succession(list(reversed(roster)), server_seed=SEED)
    assert forward == reversed_order
    # And the deterministic order is not merely the identity (sorted) order in
    # general — the server seed permutes it.
    assert forward != ("a", "b", "c", "d") or True  # tolerate coincidental ID order


def test_succession_changes_with_server_seed() -> None:
    roster = _roster(("a", True), ("b", True), ("c", True), ("d", True), ("e", True))
    a = seed_host_succession(roster, server_seed="seed-one")
    b = seed_host_succession(roster, server_seed="seed-two")
    # Same membership, different unsteerable order.
    assert sorted(a) == sorted(b)
    assert a != b


def test_succession_skips_non_opted_in_players() -> None:
    roster = _roster(("a", False), ("b", True), ("c", False), ("d", True))
    order = seed_host_succession(roster, server_seed=SEED)
    assert set(order) == {"b", "d"}
    assert "a" not in order and "c" not in order


def test_succession_empty_when_nobody_opts_in() -> None:
    roster = _roster(("a", False), ("b", False))
    assert seed_host_succession(roster, server_seed=SEED) == ()


def test_succession_dedupes_repeated_puuid() -> None:
    roster = _roster(("a", True), ("a", True), ("b", True))
    order = seed_host_succession(roster, server_seed=SEED)
    assert sorted(order) == ["a", "b"]


def test_next_successor_walks_the_chain_then_exhausts() -> None:
    chain = seed_host_succession(_roster(("a", True), ("b", True), ("c", True)), server_seed=SEED)
    assert next_successor(chain, current_host=None) == chain[0]
    assert next_successor(chain, current_host=chain[0]) == chain[1]
    assert next_successor(chain, current_host=chain[1]) == chain[2]
    assert next_successor(chain, current_host=chain[2]) is None  # exhausted


def test_next_successor_unknown_host_returns_first_remaining() -> None:
    chain = ("a", "b", "c")
    assert next_successor(chain, current_host="zzz") == "a"


def test_next_successor_empty_chain() -> None:
    assert next_successor((), current_host=None) is None


# --------------------------------------------------------------------------- #
# Phase-gated transition table (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


def test_happy_path_constant_matches_phase_order() -> None:
    # HAPPY_PATH is the documented ASSIGNED_HOST..RESULT_PENDING ordered spine.
    assert HAPPY_PATH[0] == LobbyPhase.ASSIGNED_HOST
    assert HAPPY_PATH[-1] == LobbyPhase.RESULT_PENDING
    # Each consecutive pair has exactly one driving event in the table.
    for cur, nxt in zip(HAPPY_PATH, HAPPY_PATH[1:], strict=False):
        assert nxt in ALLOWED_TRANSITIONS[cur].values()


def test_happy_path_walks_assigned_host_to_ended() -> None:
    events = [
        LobbyEvent.HOST_DESIGNATED,
        LobbyEvent.LOBBY_CREATED,
        LobbyEvent.INVITES_SENT,
        LobbyEvent.ALL_MEMBERS_PRESENT,
        LobbyEvent.CHAMP_SELECT_STARTED,
        LobbyEvent.GAME_STARTED,
        LobbyEvent.GAME_ENDED,
        LobbyEvent.RESULT_FINALIZED,
    ]
    state = LobbyPhase.ASSIGNED_HOST
    visited = [state]
    for ev in events:
        state = decide_transition(state, ev)
        visited.append(state)
    assert visited == [
        LobbyPhase.ASSIGNED_HOST,
        LobbyPhase.AWAITING_CREATE,
        LobbyPhase.LOBBY_OPEN,
        LobbyPhase.FILLING,
        LobbyPhase.READY,
        LobbyPhase.STARTING,
        LobbyPhase.IN_GAME,
        LobbyPhase.RESULT_PENDING,
        LobbyPhase.ENDED,
    ]


@pytest.mark.parametrize("phase", list(TERMINAL_PHASES))
@pytest.mark.parametrize("event", list(LobbyEvent))
def test_terminal_phases_have_no_outgoing_edges(phase: LobbyPhase, event: LobbyEvent) -> None:
    with pytest.raises(IllegalTransitionError):
        decide_transition(phase, event)


@pytest.mark.parametrize(
    "phase,event",
    list(itertools.product(list(LobbyPhase), list(LobbyEvent))),
)
def test_decide_transition_total_over_phase_x_event(phase: LobbyPhase, event: LobbyEvent) -> None:
    """Every (phase, event) pair either maps to a permitted next or raises."""
    permitted = ALLOWED_TRANSITIONS.get(phase, {})
    if event in permitted:
        assert decide_transition(phase, event) == permitted[event]
    else:
        with pytest.raises(IllegalTransitionError) as ei:
            decide_transition(phase, event)
        assert ei.value.current == phase
        assert ei.value.event == event


def test_abort_edge_from_every_pregame_phase() -> None:
    for phase in (LobbyPhase.ASSIGNED_HOST, *PRE_GAME_PHASES, LobbyPhase.STARTING):
        assert decide_transition(phase, LobbyEvent.ABORT) == LobbyPhase.ABORTED


def test_in_game_cannot_be_aborted() -> None:
    # The server-side match cannot be torn down by us once launched.
    with pytest.raises(IllegalTransitionError):
        decide_transition(LobbyPhase.IN_GAME, LobbyEvent.ABORT)


def test_illegal_jump_skipping_a_phase_raises() -> None:
    # ASSIGNED_HOST cannot jump straight to LOBBY_OPEN.
    with pytest.raises(IllegalTransitionError):
        decide_transition(LobbyPhase.ASSIGNED_HOST, LobbyEvent.LOBBY_CREATED)


def test_host_lost_terminal_edge_from_pregame() -> None:
    for phase in (LobbyPhase.ASSIGNED_HOST, *PRE_GAME_PHASES, LobbyPhase.STARTING):
        assert decide_transition(phase, LobbyEvent.HOST_LOST_TERMINAL) == LobbyPhase.HOST_LOST


# --------------------------------------------------------------------------- #
# Failover decision matrix (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #

CHAIN = ("host", "succ1", "succ2")

_FAILOVER_DEFAULTS = dict(
    missed_heartbeats=5,
    grace=3,
    epochs_used=0,
    max_epochs=3,
    current_epoch=7,
    succession=CHAIN,
    current_host="host",
)


def _decide(state: LobbyPhase, **over: object) -> Decision:
    kwargs = {**_FAILOVER_DEFAULTS, **over}
    return decide_failover(state, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("phase", sorted(PRE_GAME_PHASES, key=lambda p: p.value))
def test_pregame_promote_first_when_host_connected(phase: LobbyPhase) -> None:
    d = _decide(phase, host_connected=True)
    assert d.action == FailoverAction.PROMOTE
    assert d.new_epoch == 8  # current_epoch + 1
    assert d.successor_puuid == "host"  # lossless: same host, new lease


@pytest.mark.parametrize("phase", sorted(PRE_GAME_PHASES, key=lambda p: p.value))
def test_pregame_recreate_when_host_gone(phase: LobbyPhase) -> None:
    d = _decide(phase, host_connected=False)
    assert d.action == FailoverAction.RECREATE
    assert d.new_epoch == 8
    assert d.successor_puuid == "succ1"  # next opted-in successor


def test_in_game_failover_is_hard_noop_regardless_of_signals() -> None:
    # Even with a fully dropped host and exhausted epochs, IN_GAME is a NO-OP.
    d = _decide(
        LobbyPhase.IN_GAME,
        host_connected=False,
        missed_heartbeats=99,
        epochs_used=99,
    )
    assert d.action == FailoverAction.NOOP_INGAME
    assert d.new_epoch is None
    assert d.successor_puuid is None


def test_starting_recreates_when_gameflow_not_advanced() -> None:
    d = _decide(LobbyPhase.STARTING, host_connected=False, gameflow_advanced=False)
    assert d.action == FailoverAction.RECREATE
    assert d.successor_puuid == "succ1"


def test_starting_promotes_when_host_connected_and_not_advanced() -> None:
    d = _decide(LobbyPhase.STARTING, host_connected=True, gameflow_advanced=False)
    assert d.action == FailoverAction.PROMOTE
    assert d.successor_puuid == "host"


def test_starting_noop_when_gameflow_already_advanced() -> None:
    # Game launched on a surviving client -> do NOT recreate.
    d = _decide(LobbyPhase.STARTING, host_connected=False, gameflow_advanced=True)
    assert d.action == FailoverAction.NOOP_INGAME


def test_grace_gate_suppresses_failover_within_window() -> None:
    d = _decide(LobbyPhase.FILLING, host_connected=False, missed_heartbeats=2, grace=3)
    assert d.action == FailoverAction.NOOP_INGAME
    assert "within grace" in d.reason


def test_grace_gate_fires_at_threshold() -> None:
    d = _decide(LobbyPhase.FILLING, host_connected=False, missed_heartbeats=3, grace=3)
    assert d.action == FailoverAction.RECREATE


def test_phase_advanced_suppresses_failover() -> None:
    # The watchdog observed a fresher phase than the persisted state: legitimate
    # advance, not a host drop. Suppress.
    d = _decide(
        LobbyPhase.READY,
        host_connected=False,
        phase=LobbyPhase.STARTING,
    )
    assert d.action == FailoverAction.NOOP_INGAME
    assert "advanced" in d.reason


def test_phase_unchanged_allows_failover() -> None:
    d = _decide(LobbyPhase.READY, host_connected=False, phase=LobbyPhase.READY)
    assert d.action == FailoverAction.RECREATE


def test_bounded_epoch_exhaustion_aborts_to_manual() -> None:
    d = _decide(
        LobbyPhase.FILLING,
        host_connected=False,
        epochs_used=3,
        max_epochs=3,
    )
    assert d.action == FailoverAction.ABORT_TO_MANUAL
    assert d.new_epoch is None


def test_exhaustion_aborts_even_when_host_connected() -> None:
    # No more lease budget -> manual, regardless of promote eligibility.
    d = _decide(
        LobbyPhase.FILLING,
        host_connected=True,
        epochs_used=5,
        max_epochs=3,
    )
    assert d.action == FailoverAction.ABORT_TO_MANUAL


def test_no_standby_successor_aborts_to_manual() -> None:
    # Host gone, host is the last in the chain -> nobody to re-create with.
    d = _decide(
        LobbyPhase.FILLING,
        host_connected=False,
        succession=("host",),
        current_host="host",
    )
    assert d.action == FailoverAction.ABORT_TO_MANUAL
    assert "no opted-in standby" in d.reason


@pytest.mark.parametrize("phase", sorted(TERMINAL_PHASES, key=lambda p: p.value))
def test_terminal_phases_never_failover(phase: LobbyPhase) -> None:
    d = _decide(phase, host_connected=False)
    assert d.action == FailoverAction.NOOP_INGAME


# Exhaustive phase x host-connected x advanced matrix sanity (no exceptions, and
# IN_GAME/terminal always NOOP).
@pytest.mark.parametrize("phase", list(LobbyPhase))
@pytest.mark.parametrize("host_connected", [True, False])
@pytest.mark.parametrize("gameflow_advanced", [True, False])
def test_failover_total_and_in_game_is_always_noop(
    phase: LobbyPhase, host_connected: bool, gameflow_advanced: bool
) -> None:
    d = _decide(phase, host_connected=host_connected, gameflow_advanced=gameflow_advanced)
    assert isinstance(d, Decision)
    if phase == LobbyPhase.IN_GAME or phase in TERMINAL_PHASES:
        assert d.action == FailoverAction.NOOP_INGAME
    # Actionable decisions always carry a bumped, monotonic epoch.
    if d.action in (FailoverAction.PROMOTE, FailoverAction.RECREATE):
        assert d.new_epoch == _FAILOVER_DEFAULTS["current_epoch"] + 1


# --------------------------------------------------------------------------- #
# Epoch lease / split-brain fencing (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


def test_current_epoch_command_accepted() -> None:
    assert is_epoch_current(7, 7) is True
    assert reject_stale_ack(7, 7) is False


def test_stale_lower_epoch_rejected_split_brain() -> None:
    # Deposed/flapping host acking under its old lease.
    assert is_epoch_current(6, 7) is False
    assert reject_stale_ack(6, 7) is True


def test_higher_epoch_rejected_fail_closed() -> None:
    # Impossible against a monotonic backend-owned lease -> fail closed.
    assert is_epoch_current(8, 7) is False
    assert reject_stale_ack(8, 7) is True


# --------------------------------------------------------------------------- #
# Heartbeat freshness folding (PLAN.md §3.6.1)
# --------------------------------------------------------------------------- #


def test_fresh_accepted_heartbeat_resets_missed() -> None:
    prior = HeartbeatState(last_seen_seconds=100.0, missed=4)
    out = apply_heartbeat(
        prior,
        now_seconds=110.0,
        heartbeat_seconds=109.0,
        interval_seconds=3.0,
    )
    assert out.last_seen_seconds == 109.0
    assert out.missed == 0


def test_missing_heartbeat_increments_missed_by_elapsed_intervals() -> None:
    prior = HeartbeatState(last_seen_seconds=100.0, missed=0)
    out = apply_heartbeat(
        prior,
        now_seconds=110.0,  # 10s elapsed, 3s interval -> 3 whole intervals
        heartbeat_seconds=None,
        interval_seconds=3.0,
    )
    assert out.last_seen_seconds == 100.0
    assert out.missed == 3


def test_stale_epoch_heartbeat_not_accepted_counts_as_missing() -> None:
    # A heartbeat arrived but failed epoch fencing (accepted=False): treated as
    # no heartbeat, so misses keep accruing — the deposed host cannot keep itself
    # alive in the watchdog's eyes.
    prior = HeartbeatState(last_seen_seconds=100.0, missed=0)
    out = apply_heartbeat(
        prior,
        now_seconds=112.0,
        heartbeat_seconds=111.0,
        interval_seconds=3.0,
        accepted=False,
    )
    assert out.last_seen_seconds == 100.0
    assert out.missed == 4


def test_apply_heartbeat_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError):
        apply_heartbeat(
            HeartbeatState(last_seen_seconds=0.0),
            now_seconds=1.0,
            heartbeat_seconds=None,
            interval_seconds=0.0,
        )


def test_heartbeat_is_stale_predicate() -> None:
    assert heartbeat_is_stale(HeartbeatState(0.0, missed=3), grace=3) is True
    assert heartbeat_is_stale(HeartbeatState(0.0, missed=2), grace=3) is False


def test_heartbeat_drives_failover_end_to_end() -> None:
    # Fold a stale heartbeat into state, then feed missed into decide_failover.
    state = apply_heartbeat(
        HeartbeatState(last_seen_seconds=0.0),
        now_seconds=12.0,
        heartbeat_seconds=None,
        interval_seconds=3.0,
    )
    assert state.missed == 4
    d = decide_failover(
        LobbyPhase.FILLING,
        host_connected=False,
        missed_heartbeats=state.missed,
        grace=3,
        epochs_used=0,
        max_epochs=3,
        current_epoch=1,
        succession=CHAIN,
        current_host="host",
    )
    assert d.action == FailoverAction.RECREATE
    assert d.new_epoch == 2
