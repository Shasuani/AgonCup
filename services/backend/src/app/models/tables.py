"""ORM models for the v3 data model (PLAN.md §2).

Conventions
-----------
* **PUUID is canonical identity**; ``display_name`` / ``summoner_id`` are
  refreshable, non-identity display data.
* ``User`` / ``RiotAccount`` are **global**; everything else is **guild-scoped**
  (mixes in :class:`~app.models.base.GuildScoped`) and lands under guild RLS.
* The **same-guild invariant** is structural: child rows reference parents by a
  composite ``(id, guild_id)`` key via ``ForeignKeyConstraint`` to a
  ``UNIQUE(id, guild_id)`` on the parent, so a row can never point at another
  guild's parent.
* String state/status columns reuse the canonical enums from ``lol_schemas`` and
  are stored as ``VARCHAR + CHECK`` (``native_enum=False``) — no PG enum-type
  churn in migrations, values match the wire DTOs exactly.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from lol_schemas.enums import (
    GuildStatus,
    LobbyPhase,
    MatchResultState,
    MatchStatus,
    RegistrationStatus,
    ResultDecidedBy,
    ResultSource,
    TeamMemberRole,
    TournamentFormat,
    TournamentStatus,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, GuildScoped, TimestampMixin, created_at_col, uuid_pk


def pg_enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    """A ``VARCHAR + CHECK`` column type storing the enum *values* (not names)."""
    return SAEnum(
        py_enum,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


# ---------------------------------------------------------------------------
# Tenant anchor — the guild itself (global; created by the 0001 baseline).
# Modeled here so FK targets resolve and metadata is complete; the core
# migration does NOT re-create it (checkfirst skips the existing table).
# ---------------------------------------------------------------------------
class Guild(Base):
    __tablename__ = "guilds"
    __table_args__ = (
        CheckConstraint("status in ('active','suspended','removed')", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    discord_guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_central: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false())
    status: Mapped[GuildStatus] = mapped_column(
        pg_enum(GuildStatus, "guild_status"), nullable=False, server_default=GuildStatus.ACTIVE.value
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Global entities (NOT guild-scoped) — one person/account across all guilds
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    discord_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)


class RiotAccount(Base):
    __tablename__ = "riot_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Canonical, immutable identity key (Account-V1 by-riot-id).
    puuid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # LCU invites are by summonerId, so it is stored (nullable until resolved).
    summoner_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Refreshable display data — NEVER matched on for identity.
    riot_id_game_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    riot_id_tag_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Guild-scoped entities
# ---------------------------------------------------------------------------
class Team(GuildScoped, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        # Target of the same-guild composite FKs from registrations/matches.
        UniqueConstraint("id", "guild_id", name="uq_teams_id_guild"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    captain_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class TeamMember(Base):
    """Membership row. Guild-scoped (carries ``guild_id``) + same-guild FK to its
    team, so RLS isolates it and it can only reference a same-guild team.
    """

    __tablename__ = "team_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id", "guild_id"],
            ["teams.id", "teams.guild_id"],
            name="fk_team_members_team_guild",
            ondelete="CASCADE",
        ),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Denormalized for RLS + the composite FK above (mixin would add a 2nd FK).
    guild_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[TeamMemberRole] = mapped_column(
        pg_enum(TeamMemberRole, "team_member_role"), nullable=False
    )


class Tournament(GuildScoped, Base):
    __tablename__ = "tournaments"
    __table_args__ = (
        UniqueConstraint("id", "guild_id", name="uq_tournaments_id_guild"),
        CheckConstraint("max_teams > 0 AND max_teams <= 16", name="max_teams_bound"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TournamentStatus] = mapped_column(
        pg_enum(TournamentStatus, "tournament_status"),
        nullable=False,
        server_default=TournamentStatus.DRAFT.value,
    )
    format: Mapped[TournamentFormat] = mapped_column(
        pg_enum(TournamentFormat, "tournament_format"),
        nullable=False,
        server_default=TournamentFormat.SINGLE_ELIMINATION.value,
    )
    max_teams: Mapped[int] = mapped_column(Integer, nullable=False, server_default="16")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_shared_to_central: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.false()
    )
    ruleset_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = created_at_col()


class Registration(GuildScoped, Base):
    __tablename__ = "registrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id", "guild_id"],
            ["teams.id", "teams.guild_id"],
            name="fk_registrations_team_guild",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_registrations_tournament_guild",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tournament_id", "team_id", name="uq_registrations_tournament_team"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[RegistrationStatus] = mapped_column(
        pg_enum(RegistrationStatus, "registration_status"),
        nullable=False,
        server_default=RegistrationStatus.PENDING.value,
    )


class Match(GuildScoped, Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("id", "guild_id", name="uq_matches_id_guild"),
        ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_matches_tournament_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    bracket_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    team_a: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    team_b: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    winner_team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[MatchStatus] = mapped_column(
        pg_enum(MatchStatus, "match_status"),
        nullable=False,
        server_default=MatchStatus.PENDING.value,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MatchLobby(GuildScoped, Base):
    """Autonomous lobby orchestration row (PLAN.md §3.6.1). One per match."""

    __tablename__ = "match_lobbies"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_match_lobbies_match"),
        ForeignKeyConstraint(
            ["match_id", "guild_id"],
            ["matches.id", "matches.guild_id"],
            name="fk_match_lobbies_match_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    match_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    host_puuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deterministic, server-seeded succession order (NOT "first checked-in").
    host_succession: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    host_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lobby_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    lobby_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    lcu_lobby_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    lcu_game_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[LobbyPhase] = mapped_column(
        pg_enum(LobbyPhase, "lobby_phase"),
        nullable=False,
        server_default=LobbyPhase.ASSIGNED_HOST.value,
    )
    last_host_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_col()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientSession(GuildScoped, Base):
    """Desktop-client liveness + consent record (PLAN.md §3.6.1 / §3.3)."""

    __tablename__ = "client_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    puuid: Mapped[str] = mapped_column(Text, nullable=False)
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("matches.id", ondelete="SET NULL"), nullable=True
    )
    lcu_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false())
    gameflow_phase: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    host_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false())


class ResultReport(GuildScoped, Base):
    """One participant client's forgeable end-of-game report (PLAN.md §3.6.6)."""

    __tablename__ = "result_reports"
    __table_args__ = (
        UniqueConstraint(
            "lcu_game_id", "reporter_puuid", "source", name="uq_result_reports_game_reporter_source"
        ),
        ForeignKeyConstraint(
            ["match_id", "guild_id"],
            ["matches.id", "matches.guild_id"],
            name="fk_result_reports_match_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    match_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lobby_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("match_lobbies.id", ondelete="SET NULL"), nullable=True
    )
    lcu_game_id: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_puuid: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[ResultSource] = mapped_column(pg_enum(ResultSource, "result_source"), nullable=False)
    claimed_winner_team: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reported_kda: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    kill_stream: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    anomaly_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MatchResult(GuildScoped, Base):
    """The decided result for a match — provisional/confirmed/disputed (PLAN.md §3.6.6)."""

    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_match_results_match"),
        ForeignKeyConstraint(
            ["match_id", "guild_id"],
            ["matches.id", "matches.guild_id"],
            name="fk_match_results_match_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    match_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lobby_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    agreed_game_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    winner_team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decided_by: Mapped[ResultDecidedBy | None] = mapped_column(
        pg_enum(ResultDecidedBy, "result_decided_by"), nullable=True
    )
    quorum_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    admin_override_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    admin_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fail closed: a freshly-created result is disputed until quorum confirms it.
    state: Mapped[MatchResultState] = mapped_column(
        pg_enum(MatchResultState, "match_result_state"),
        nullable=False,
        server_default=MatchResultState.DISPUTED.value,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResultNonce(GuildScoped, Base):
    """Single-use, short-TTL freshness nonce per (match, puuid) (PLAN.md §3.6.6)."""

    __tablename__ = "result_nonces"
    __table_args__ = (UniqueConstraint("nonce", name="uq_result_nonces_nonce"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    match_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    puuid: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    lcu_game_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Federation / global side tables (special isolation — NOT standard guild RLS)
# ---------------------------------------------------------------------------
class CentralFeedItem(Base):
    """Consented, field-whitelisted projection into the central feed (PLAN.md §3.2).

    Cross-tenant by design (it IS the central projection); guarded by a separate
    federation policy, not the per-guild ``guild_isolation`` policy. Phase 1
    creates the table; the federation policy lands with the central-feed feature.
    """

    __tablename__ = "central_feed_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_guild_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    projected_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    published_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlayerCentralVisibility(Base):
    __tablename__ = "player_central_visibility"
    __table_args__ = (
        CheckConstraint(
            "visibility in ('team_only','show_riot_id')", name="visibility_allowed"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="team_only")


class VerificationAttempt(Base):
    """One-time Account-V1 ownership challenge (PLAN.md §3.1). Global (account linking)."""

    __tablename__ = "verification_attempts"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','verified','expired','failed')", name="status_allowed"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    puuid: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    target_icon_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class AuditLog(Base):
    """Append-only audit trail (PLAN.md §2). ``guild_id`` nullable (system events).

    Isolation is role-aware (platform operators read all; guild admins read their
    guild) rather than the strict per-guild policy, so it is handled separately
    from the standard ``guild_isolation`` set. Phase 1 creates the table.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("actor_kind in ('platform','guild','system')", name="actor_kind_allowed"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    guild_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("guilds.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Tables that get the standard per-guild RLS policy (enumerated for the migration
# and the isolation test matrix — a new guild-scoped table must be added here).
GUILD_ISOLATED_TABLES: tuple[str, ...] = (
    "teams",
    "team_members",
    "tournaments",
    "registrations",
    "matches",
    "match_lobbies",
    "client_sessions",
    "result_reports",
    "match_results",
    "result_nonces",
)
