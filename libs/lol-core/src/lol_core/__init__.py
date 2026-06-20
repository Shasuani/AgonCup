"""lol_core — domain enums, business rules, bracket engine, and result adjudication.

No web framework imports. Depends on ``lol_schemas`` for canonical DTO types.
"""

from __future__ import annotations

from .adjudication import (
    QUORUM_THRESHOLD,
    TOTAL_PARTICIPANTS,
    AdjudicationOutcome,
    AdjudicationResult,
    adjudicate_result,
)
from .bracket import (
    MAX_TEAMS,
    MIN_TEAMS,
    Bracket,
    BracketMatch,
    BracketRound,
    generate_single_elimination,
)
from .orchestration import (
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

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # bracket engine
    "Bracket",
    "BracketRound",
    "BracketMatch",
    "generate_single_elimination",
    "MIN_TEAMS",
    "MAX_TEAMS",
    # adjudication
    "adjudicate_result",
    "AdjudicationOutcome",
    "AdjudicationResult",
    "QUORUM_THRESHOLD",
    "TOTAL_PARTICIPANTS",
    # autonomous-lobby orchestration state machine
    "RosterMember",
    "seed_host_succession",
    "next_successor",
    "HAPPY_PATH",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_PHASES",
    "PRE_GAME_PHASES",
    "LobbyEvent",
    "IllegalTransitionError",
    "decide_transition",
    "FailoverAction",
    "Decision",
    "decide_failover",
    "is_epoch_current",
    "reject_stale_ack",
    "HeartbeatState",
    "apply_heartbeat",
    "heartbeat_is_stale",
]
