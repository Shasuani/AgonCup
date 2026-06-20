"""Domain enums for lol_core.

The canonical enum definitions live in ``lol_schemas`` (the single Python type
source). They are re-exported here so domain-logic callers have one import
surface (``from lol_core.enums import LobbyPhase``) without reaching into the
DTO package directly.
"""

from __future__ import annotations

from lol_schemas import (
    GuildStatus,
    LobbyPhase,
    MatchResultState,
    MatchStatus,
    RegistrationStatus,
    ResultDecidedBy,
    ResultSource,
    RiotRegion,
    TeamMemberRole,
    TournamentFormat,
    TournamentStatus,
)

__all__ = [
    "GuildStatus",
    "TeamMemberRole",
    "TournamentStatus",
    "TournamentFormat",
    "RegistrationStatus",
    "MatchStatus",
    "LobbyPhase",
    "ResultSource",
    "ResultDecidedBy",
    "MatchResultState",
    "RiotRegion",
]
