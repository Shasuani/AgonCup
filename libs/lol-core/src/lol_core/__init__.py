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
]
