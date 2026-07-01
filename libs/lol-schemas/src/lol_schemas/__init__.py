"""lol_schemas — canonical Pydantic v2 DTOs for LeagueOfLeagues (v3 model).

The single canonical Python type source. Import models directly from the package
root, e.g. ``from lol_schemas import TournamentDTO, LobbyPhase``.
"""

from __future__ import annotations

from .dtos import (
    MAX_TEAMS,
    DesktopPairAccountDTO,
    DesktopPairRequestDTO,
    DesktopPairResponseDTO,
    DesktopPairUserDTO,
    DesktopRegistrationDTO,
    DesktopVerifyRequestDTO,
    DesktopVerifyResponseDTO,
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
    VerifyLcuRequestDTO,
)
from .enums import (
    GameMode,
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
    UserGuildRoleName,
)

__version__ = "0.1.0"

__all__ = [
    "GameMode",
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
    "DesktopRegistrationDTO",
    "DesktopVerifyRequestDTO",
    "DesktopVerifyResponseDTO",
    "VerifyLcuRequestDTO",
    "DesktopPairRequestDTO",
    "DesktopPairUserDTO",
    "DesktopPairAccountDTO",
    "DesktopPairResponseDTO",
    # Enums
    "GuildStatus",
    "TeamMemberRole",
    "UserGuildRoleName",
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
