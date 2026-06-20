"""lol_schemas — canonical Pydantic v2 DTOs for LeagueOfLeagues (v3 model).

The single canonical Python type source. Import models directly from the package
root, e.g. ``from lol_schemas import TournamentDTO, LobbyPhase``.
"""

from __future__ import annotations

from .dtos import (
    MAX_TEAMS,
    GuildDTO,
    MatchDTO,
    MatchLobbyDTO,
    MatchResultDTO,
    PlayerKDA,
    QuorumDetail,
    ResultReportDTO,
    RiotAccountDTO,
    TeamDTO,
    TeamMemberDTO,
    TournamentDTO,
    UserDTO,
)
from .enums import (
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

__version__ = "0.1.0"

__all__ = [
    "MAX_TEAMS",
    "__version__",
    # DTOs
    "GuildDTO",
    "UserDTO",
    "RiotAccountDTO",
    "TeamDTO",
    "TeamMemberDTO",
    "TournamentDTO",
    "MatchDTO",
    "MatchLobbyDTO",
    "ResultReportDTO",
    "PlayerKDA",
    "QuorumDetail",
    "MatchResultDTO",
    # Enums
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
