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
# Desktop code + LCU account verification (replaces the icon challenge)        #
# --------------------------------------------------------------------------- #
class DesktopRegistrationDTO(_Base):
    """Result of ``/register`` minting a one-time desktop-link code (PLAN.md §3.1).

    The user runs ``/register`` in Discord; the backend mints a short-TTL,
    single-use ``code`` bound to (discord user, origin guild). The user types it
    into the desktop app, which reads the local LCU PUUID and completes the link.
    """

    attempt_id: UUID
    code: str = Field(..., description="Grouped XXXX-XXXX one-time link code (display form).")
    expires_at: datetime
    origin_guild_id: UUID | None = None
    instruction: str = Field(..., description="Short human instruction for the user.")


class DesktopVerifyRequestDTO(_Base):
    """Desktop -> backend verify body. The ``code`` IS the credential (no JWT).

    The desktop reads the Riot ID (``game_name``/``tag_line``) and platform
    ``region`` from the local LCU current-summoner + region-locale; the user
    supplies the ``code`` from Discord. The backend resolves the **canonical**
    78-char PUUID server-side via Account-V1 by-riot-id (the double
    verification) — the desktop no longer sends the canonical puuid. The
    advisory ``lcu_puuid`` (a 36-char LCU UUID, NOT the canonical identity) and
    ``summoner_id`` (for LCU invites) are optional secondary signals.
    """

    code: str = Field(
        ..., min_length=8, max_length=12, description="Hyphenated or not; normalized."
    )
    game_name: str = Field(..., min_length=1, max_length=64)
    tag_line: str = Field(..., min_length=1, max_length=16)
    region: str = Field(..., min_length=2, max_length=8, description="Platform route e.g. 'eun1'.")
    lcu_puuid: str | None = Field(
        default=None, max_length=128, description="Advisory LCU UUID (not canonical)."
    )
    summoner_id: str | None = Field(
        default=None, max_length=64, description="LCU summonerId (for invites)."
    )


class DesktopVerifyResponseDTO(_Base):
    """Successful desktop verify result: the account is now linked + verified.

    Carries the resolved canonical Riot ID/region and a best-effort Solo/Duo
    rank snapshot (``None`` when unranked or the rank fetch failed).
    """

    account_id: UUID
    puuid: str = Field(..., description="Canonical 78-char Account-V1 puuid.")
    verified: bool = Field(..., description="Always true on a 200 response.")
    origin_guild_id: UUID | None = None
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    # Solo/Duo rank snapshot (None when unranked or rank fetch failed):
    tier: str | None = None
    division: str | None = None
    league_points: int | None = None
    rank_text: str = Field(default="Unranked", description="Display string e.g. 'Gold II, 45 LP'.")


# --------------------------------------------------------------------------- #
# Desktop pairing — code -> session bootstrap (PLAN.md §3.1)                   #
# --------------------------------------------------------------------------- #
class VerifyLcuRequestDTO(_Base):
    """Authenticated desktop -> backend LCU verify body (``DesktopVerifyRequestDTO``
    minus ``code``).

    The session is established separately via ``/auth/desktop-pair``; the Riot link
    then runs authenticated (JWT bearer), so the one-time code is no longer carried
    here. The desktop reads the Riot ID (``game_name``/``tag_line``) + platform
    ``region`` from the local LCU; the backend resolves the canonical PUUID
    server-side via Account-V1 (the double verification). ``lcu_puuid`` (advisory
    36-char LCU UUID, NOT canonical) and ``summoner_id`` (LCU invites) are optional.
    """

    game_name: str = Field(..., min_length=1, max_length=64)
    tag_line: str = Field(..., min_length=1, max_length=16)
    region: str = Field(..., min_length=2, max_length=8, description="Platform route e.g. 'eun1'.")
    lcu_puuid: str | None = Field(
        default=None, max_length=128, description="Advisory LCU UUID (not canonical)."
    )
    summoner_id: str | None = Field(
        default=None, max_length=64, description="LCU summonerId (for invites)."
    )


class DesktopPairRequestDTO(_Base):
    """Desktop -> backend pairing body: exchange a one-time code for a session.

    The ``code`` is the bot-minted ``desktop_code`` ``VerificationAttempt`` value
    (the SAME code minted by ``/register`` // the Discord ``accounts/link`` bridge);
    it is single-use, short-TTL, and is now a SESSION bootstrap only — the Riot link
    moved to the authenticated ``/accounts/verify-lcu``. ``device_label`` is an
    optional human label persisted on the issued refresh token (device management).
    """

    code: str = Field(..., min_length=8, max_length=12, description="Hyphenated or not.")
    device_label: str | None = Field(
        default=None, max_length=64, description='Human device label, e.g. "Kostas\' MacBook".'
    )


class DesktopPairUserDTO(_Base):
    """The authenticated user summary returned by ``/auth/desktop-pair``."""

    user_id: UUID
    discord_user_id: str
    display_name: str


class DesktopPairAccountDTO(_Base):
    """The linked-account summary returned alongside a pairing session.

    ``None`` when the user has no ``RiotAccount`` yet (they pair first, then link
    their League account from the desktop via ``/accounts/verify-lcu``).
    """

    linked: bool
    puuid: str | None = None
    game_name: str | None = None
    tag_line: str | None = None
    region: str | None = None
    verified: bool = False
    rank_text: str = "Unranked"


class DesktopPairResponseDTO(_Base):
    """Successful ``/auth/desktop-pair`` result: a session + user/account summary."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - scheme name, not a secret
    user: DesktopPairUserDTO
    account: DesktopPairAccountDTO | None = None


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
