"""ORM models package (PLAN.md §2).

``Base.metadata`` is the single schema source of truth wired into Alembic
(``alembic/env.py``). Import models from here.
"""

from __future__ import annotations

from app.models.base import Base, GuildScoped
from app.models.tables import (
    GUILD_ISOLATED_TABLES,
    AuditLog,
    CentralFeedItem,
    ClientSession,
    Guild,
    Match,
    MatchLobby,
    MatchResult,
    OAuthCredential,
    PlayerCentralVisibility,
    RefreshToken,
    Registration,
    ResultNonce,
    ResultReport,
    RiotAccount,
    Team,
    TeamMember,
    Tournament,
    TournamentHide,
    TournamentParticipant,
    User,
    UserGuildRole,
    VerificationAttempt,
)

__all__ = [
    "Base",
    "GuildScoped",
    "GUILD_ISOLATED_TABLES",
    "AuditLog",
    "CentralFeedItem",
    "ClientSession",
    "Guild",
    "Match",
    "MatchLobby",
    "MatchResult",
    "OAuthCredential",
    "PlayerCentralVisibility",
    "RefreshToken",
    "Registration",
    "ResultNonce",
    "ResultReport",
    "RiotAccount",
    "Team",
    "TeamMember",
    "Tournament",
    "TournamentHide",
    "TournamentParticipant",
    "User",
    "UserGuildRole",
    "VerificationAttempt",
]
