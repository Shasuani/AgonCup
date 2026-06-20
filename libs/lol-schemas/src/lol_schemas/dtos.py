"""Canonical Pydantic v2 DTOs reflecting the v3 autonomous data model.

These are representative wire/transfer objects for the core entities described in
PLAN.md §2. They are intentionally framework-light (pydantic only) and carry no
persistence concerns. Field invariants documented in the plan are encoded as
validators/constraints where cheap to do so (e.g. ``max_teams`` capped at 16,
``puuid`` canonical/required, the both-team quorum floor on result reports).

NOTE: This is Phase-0 scaffolding. The set of models is representative, not
exhaustive — additional entities (Registration, ClientSession, AuditLog, etc.)
follow the same conventions and are added as the slices that own them land.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    GuildStatus,
    LobbyPhase,
    MatchResultState,
    MatchStatus,
    ResultDecidedBy,
    ResultSource,
    RiotRegion,
    TeamMemberRole,
    TournamentFormat,
    TournamentStatus,
)

# Defaulting/maximum number of teams per tournament (PLAN.md §2 "max_teams(=16)").
MAX_TEAMS: int = 16


class _Base(BaseModel):
    """Shared base: forbid unknown fields, validate on assignment, use enum values."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
        str_strip_whitespace=True,
    )


# --------------------------------------------------------------------------- #
# Global entities (NOT guild-scoped)                                          #
# --------------------------------------------------------------------------- #
class UserDTO(_Base):
    """A global platform user, keyed to a Discord identity (PLAN.md §2 ``User``)."""

    id: UUID
    discord_user_id: str = Field(..., description="Snowflake of the Discord user.")
    display_name: str
    created_at: datetime


class RiotAccountDTO(_Base):
    """A linked Riot account. ``puuid`` is the canonical, immutable identity key.

    ``display_name`` (gameName/tagLine) is stale, refreshable, NON-identity data
    and is never matched on for identity (PLAN.md §2 key rules).
    """

    id: UUID
    user_id: UUID
    puuid: str = Field(..., min_length=1, description="Canonical immutable identity key.")
    summoner_id: str | None = Field(
        default=None, description="Stored because LCU invites are by summonerId."
    )
    game_name: str | None = None
    tag_line: str | None = None
    region: RiotRegion = RiotRegion.AMERICAS
    verified: bool = False
    verified_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Guild-scoped entities                                                       #
# --------------------------------------------------------------------------- #
class GuildDTO(_Base):
    """A Discord guild (tenant). All non-global entities are scoped by ``id``."""

    id: UUID
    discord_guild_id: str
    name: str
    is_central: bool = False
    status: GuildStatus = GuildStatus.ACTIVE
    invited_at: datetime
    removed_at: datetime | None = None


class TeamMemberDTO(_Base):
    user_id: UUID
    role: TeamMemberRole = TeamMemberRole.PLAYER


class TeamDTO(_Base):
    """A team within a guild (PLAN.md §2 ``Team``; UNIQUE(id, guild_id))."""

    id: UUID
    guild_id: UUID
    name: str
    captain_user_id: UUID
    members: list[TeamMemberDTO] = Field(default_factory=list)
    created_at: datetime


class TournamentDTO(_Base):
    """A tournament. ``max_teams`` defaults to and is capped at 16 (PLAN.md §2)."""

    id: UUID
    guild_id: UUID
    name: str
    status: TournamentStatus = TournamentStatus.DRAFT
    format: TournamentFormat = TournamentFormat.SINGLE_ELIMINATION
    max_teams: int = Field(default=MAX_TEAMS, ge=2, le=MAX_TEAMS)
    start_at: datetime | None = None
    is_shared_to_central: bool = False
    ruleset_json: dict[str, object] = Field(default_factory=dict)


class MatchDTO(_Base):
    """A bracket match (PLAN.md §2 ``Match``)."""

    id: UUID
    guild_id: UUID
    tournament_id: UUID
    round: int = Field(..., ge=1)
    bracket_slot: int = Field(..., ge=0)
    team_a: UUID | None = None
    team_b: UUID | None = None
    winner_team_id: UUID | None = None
    status: MatchStatus = MatchStatus.PENDING
    scheduled_at: datetime | None = None


class MatchLobbyDTO(_Base):
    """Per-match LCU lobby state (PLAN.md §2 ``MatchLobby`` / §3.6.1).

    ``host_epoch`` is the monotonic designation lease; ``host_succession`` is the
    deterministic, server-seeded ordering bound to the roster.
    """

    id: UUID
    guild_id: UUID
    match_id: UUID
    host_puuid: str | None = None
    host_succession: list[str] = Field(default_factory=list)
    host_epoch: int = Field(default=0, ge=0)
    lobby_name: str
    lobby_password: str | None = None
    lcu_lobby_id: str | None = None
    lcu_game_id: str | None = None
    phase: LobbyPhase = LobbyPhase.ASSIGNED_HOST
    last_host_heartbeat_at: datetime | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Result corroboration (PLAN.md §3.6.6)                                       #
# --------------------------------------------------------------------------- #
class PlayerKDA(_Base):
    """Per-player kills/deaths/assists as read from the eog stats block."""

    puuid: str = Field(..., min_length=1)
    kills: int = Field(..., ge=0)
    deaths: int = Field(..., ge=0)
    assists: int = Field(..., ge=0)


class ResultReportDTO(_Base):
    """A single participant's forgeable, identity-bound end-of-game report.

    One of up to 10 independent reports keyed by ``(lcu_game_id, reporter_puuid,
    source)``. ``lcu_game_id`` is a corroboration value, NOT a trusted unique key
    (PLAN.md §2 result-integrity rules).
    """

    id: UUID | None = None
    guild_id: UUID
    match_id: UUID
    lobby_id: UUID | None = None
    lcu_game_id: str = Field(..., min_length=1, description="Client-reported, cross-checked.")
    reporter_puuid: str = Field(..., min_length=1)
    source: ResultSource = ResultSource.EOG_STATS_BLOCK
    winner: UUID | None = Field(
        default=None, description="Claimed winning team id (claimed_winner_team)."
    )
    per_player_kda: list[PlayerKDA] = Field(default_factory=list)
    nonce: str | None = Field(default=None, description="Single-use server-issued freshness nonce.")
    schema_version: str | None = None
    received_at: datetime | None = None


class QuorumDetail(_Base):
    """Audit detail of how a result was adjudicated (PLAN.md §2 ``quorum_detail``)."""

    reports_counted: int = Field(default=0, ge=0)
    both_teams_represented: bool = False
    anomaly_summary: dict[str, object] = Field(default_factory=dict)


class MatchResultDTO(_Base):
    """The adjudicated result for a match (PLAN.md §2 ``MatchResult``; UNIQUE(match_id)).

    Becomes ``PROVISIONAL`` on quorum (>=6/10 agreeing, both teams represented, no
    high-severity anomaly); otherwise ``DISPUTED`` -> admin override.
    """

    id: UUID | None = None
    guild_id: UUID
    match_id: UUID
    tournament_id: UUID
    lobby_id: UUID | None = None
    agreed_game_id: str | None = None
    winner_team_id: UUID | None = None
    decided_by: ResultDecidedBy = ResultDecidedBy.QUORUM
    quorum_detail: QuorumDetail = Field(default_factory=QuorumDetail)
    admin_override_by: UUID | None = None
    admin_override_reason: str | None = None
    state: MatchResultState = MatchResultState.PROVISIONAL
    decided_at: datetime | None = None
