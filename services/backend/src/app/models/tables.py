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
    GameMode,
    GuildStatus,
    LobbyPhase,
    MatchResultState,
    MatchStatus,
    RegistrationStatus,
    ResultDecidedBy,
    ResultSource,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentFormat,
    TournamentStatus,
    UserGuildRoleName,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
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
    __table_args__ = (CheckConstraint("status in ('active','suspended','removed')", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    discord_guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_central: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    status: Mapped[GuildStatus] = mapped_column(
        pg_enum(GuildStatus, "guild_status"),
        nullable=False,
        server_default=GuildStatus.ACTIVE.value,
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
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The guild the user ran /register from, copied from the verification attempt
    # at verify time (the attempt may later be pruned). Plain FK; global table.
    origin_guild_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Advisory 36-char LCU UUID (NOT the canonical PUUID) — a secondary possession
    # signal sent by the desktop at verify time (PLAN.md §3.1). Nullable.
    lcu_puuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Best-effort Solo/Duo rank snapshot taken at desktop-verify time (League-V4).
    # All nullable: unranked / fetch-failed leaves them NULL (PLAN.md §3.5).
    rank_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank_division: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank_lp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_queue: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Auth: refresh tokens + OAuth credentials (GLOBAL — not guild-scoped).
# Tokens are stored ENCRYPTED/HASHED, never in plaintext (PLAN.md §7).
# ---------------------------------------------------------------------------
class RefreshToken(Base):
    """A rotating, revocable refresh token (PLAN.md §3.1 auth).

    Global (a refresh session is per *user*, not per guild). The token itself is
    NEVER stored — only a SHA-256 ``token_hash`` of the opaque secret, so a DB
    leak can't replay sessions. Rotation forms a *family*: each refresh issues a
    new row whose ``rotated_from_id`` points at its predecessor and which shares
    the original ``family_id``. Presenting an already-rotated (revoked) token is
    **reuse** → the whole family is revoked (theft detection).
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the opaque token secret — never the secret itself.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # All tokens descended from one login share a family; reuse revokes the family.
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # The predecessor token this one was rotated from (NULL for the login root).
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Desktop-pairing device metadata (migration 0006). NULL for OAuth-login /
    # dev-login / refresh-rotation roots; set by ``/auth/desktop-pair`` and carried
    # forward across rotation so a device label survives the token family.
    device_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class OAuthCredential(Base):
    """A user's stored OAuth credential for an external provider (e.g. discord).

    Global. Access/refresh tokens are persisted ENCRYPTED at rest via
    ``app.security.crypto.encrypt`` (PLAN.md §7) — the ``*_enc`` columns hold
    opaque Fernet tokens, never plaintext.
    """

    __tablename__ = "oauth_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_oauth_credentials_user_provider"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # Fernet-encrypted token blobs (see app.security.crypto). Refresh may be null.
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Guild-scoped entities
# ---------------------------------------------------------------------------
class UserGuildRole(GuildScoped, Base):
    """Per-(user, guild) authorization role (PLAN.md §3.1 auth, §11 decision 6).

    Guild-scoped: the role lookup is naturally per-current-guild, so it lands
    under the standard ``guild_isolation`` RLS policy. ``platform_operator`` rows
    still carry a ``guild_id`` (the guild they were granted in) but the RBAC layer
    treats that role as cross-tenant.
    """

    __tablename__ = "user_guild_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "role", name="uq_user_guild_roles_user_guild_role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[UserGuildRoleName] = mapped_column(
        pg_enum(UserGuildRoleName, "user_guild_role_name"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_col()


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
    # Shareable capability: the captain hands this to players so they can join the
    # team from the desktop client (PLAN.md §3.1). Redeemed only under the acting
    # guild (RLS), so it cannot cross tenants. Unique; NULL only for legacy rows.
    join_code: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # True for the one-player team auto-created behind a SOLO enrolment (1v1). The
    # player never creates or sees it — it exists so bracket/lobby/results can keep
    # keying on team_id. Every "real team" query filters it out (teams listings,
    # and _resolve_captain_team, which would otherwise read a 1v1 player as the
    # captain of an extra team).
    is_solo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


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
    # Approval lifecycle: a code-redeeming player lands PENDING until the captain
    # approves (→ ACTIVE) or rejects (row deleted). Captain rows are ACTIVE.
    status: Mapped[TeamMemberStatus] = mapped_column(
        pg_enum(TeamMemberStatus, "team_member_status"),
        nullable=False,
        server_default=TeamMemberStatus.ACTIVE.value,
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
    # The custom-game shape (5v5 Rift vs 1v1 ARAM) — drives lobby config + quorum.
    game_mode: Mapped[GameMode] = mapped_column(
        pg_enum(GameMode, "game_mode"),
        nullable=False,
        server_default=GameMode.SUMMONERS_RIFT_5V5.value,
    )
    # Series length: a match is won by the first team to (best_of // 2 + 1) games.
    # 1 = single game (default); 3 = best-of-three; 5 = best-of-five.
    best_of: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Riot platform the tournament is played on (PlatformRoute value, e.g. "euw1"
    # / "eun1") — same vocabulary as RiotAccount.region. Chosen at creation; the
    # join/lock-in gates require entrants to hold a verified account on it (an LCU
    # invite is per-server, so a cross-region player physically cannot be pulled
    # into the lobby). NULL = legacy/no restriction. KR is rejected by the service
    # (Korea is out of scope — docs/compliance-checklist.md).
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_teams: Mapped[int] = mapped_column(Integer, nullable=False, server_default="16")
    # Scheduling (PLAN.md §3.2). ``registration_opens_at`` gates enrolment (join is
    # rejected before it) and, once reached, the scheduler flips DRAFT→OPEN and
    # announces. ``start_at`` is the scheduled kickoff; ``start_announced_at`` is the
    # once-only guard so the "starting" announcement fires a single time. All UTC,
    # all nullable (an unscheduled tournament opens immediately and never auto-announces).
    registration_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_announced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_shared_to_central: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    # Shareable capability the admin mints on the bot and hands to players; they
    # redeem it in the desktop client to enrol (PLAN.md §3.1). Redeemed only under
    # the acting guild (RLS). Unique; NULL only for legacy rows.
    join_code: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
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


class TournamentParticipant(GuildScoped, Base):
    """A user enrolled into a tournament via its join code (PLAN.md §3.1).

    Redeeming a tournament's ``join_code`` from the desktop client creates one of
    these (``team_id`` NULL = free agent). Joining/forming a team later fills
    ``team_id``. Guild-scoped (RLS) + a same-guild composite FK to the tournament,
    so a participant can only ever reference a same-guild tournament — cross-server
    participation is structurally impossible. ``(tournament_id, user_id)`` is unique
    so redeeming the same code twice is idempotent, not a duplicate.
    """

    __tablename__ = "tournament_participants"
    __table_args__ = (
        UniqueConstraint(
            "tournament_id", "user_id", name="uq_tournament_participants_tournament_user"
        ),
        ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_tournament_participants_tournament_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The team this participant ended up on (NULL = free agent). Plain column (the
    # team membership itself is the source of truth via team_members); this is a
    # convenience denormalization for the "my tournaments" view.
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # When this individual player locked in. Lock-in is per PLAYER, not per team:
    # the captain redeems the code and everyone on the roster is entered, but each
    # of them still confirms they are actually here. The team's Registration flips
    # to CHECKED_IN only once every active member has (see `check_in_self`).
    locked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class TournamentHide(GuildScoped, Base):
    """One user's "stop showing me this tournament" (PLAN.md §3.1).

    A player's ``/tournaments/mine`` list only ever grows: every tournament they
    ever entered stays in it forever, and after a season of them the list is
    unreadable. This row is the per-user dismissal — it hides the tournament from
    *that* user's list and nobody else's. It is deliberately NOT a deletion: the
    tournament, its bracket and its results are the guild's record, and an admin
    still sees everything.

    Only a **terminal** tournament (completed / cancelled) may be hidden — the
    service enforces it. Hiding one you still have to play would silently remove
    your own way back to it.

    Guild-scoped (RLS) + a same-guild composite FK to the tournament, so the row
    dies with the tournament and can never point at another guild's.
    ``(tournament_id, user_id)`` is unique: hiding twice is idempotent.
    """

    __tablename__ = "tournament_hides"
    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_tournament_hides_tournament_user"),
        ForeignKeyConstraint(
            ["tournament_id", "guild_id"],
            ["tournaments.id", "tournaments.guild_id"],
            name="fk_tournament_hides_tournament_guild",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = created_at_col()


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
    # Best-of-N series state: games won by each side, and the LCU game ids already
    # counted (so a re-sent report for a decided game never double-counts).
    wins_a: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    wins_b: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    scored_game_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Match-ready gate (edge-cases §8.5): a 5-minute window armed when both teams
    # are present; each captain stamps their side's *_ready_at. A side that readied
    # while the opponent did not wins by walkover at the deadline.
    ready_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    team_a_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_b_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    lcu_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    gameflow_phase: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    host_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


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
    source: Mapped[ResultSource] = mapped_column(
        pg_enum(ResultSource, "result_source"), nullable=False
    )
    claimed_winner_team: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
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
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        CheckConstraint("visibility in ('team_only','show_riot_id')", name="visibility_allowed"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tournament_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="team_only")


class VerificationAttempt(Base):
    """One-time account-ownership challenge (PLAN.md §3.1). Global (account linking).

    Two flavours (``verify_kind``):

    * ``icon`` — legacy Account-V1 + Summoner-V4 profile-icon challenge: a random
      ``target_icon_id`` + single-use ``nonce``, polled by the worker.
    * ``desktop_code`` — the current flow: a one-time human-typeable ``code`` bound
      to (user, ``origin_guild_id``); the desktop reads the LCU PUUID and POSTs it
      back to complete the link. No icon, no nonce, no Account-V1 dependency.

    ``puuid``/``nonce``/``target_icon_id`` are nullable so code-mint rows (which do
    not know the PUUID until the desktop POSTs, and never use an icon/nonce) are
    representable; icon rows still set all three.
    """

    __tablename__ = "verification_attempts"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','verified','expired','failed')", name="status_allowed"
        ),
        CheckConstraint("verify_kind in ('icon','desktop_code')", name="verify_kind_allowed"),
        # At most one LIVE desktop code at a time (expired/consumed/NULL rows are
        # ignored). Migration 0004 created this partial unique index but it was
        # never mirrored here — so any database built from ``Base.metadata``
        # (which is what migration 0002 does) silently lacked it, leaving the
        # single-active-code invariant unenforced at the DB level.
        Index(
            "uq_verification_attempts_code_active",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL AND status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # PUUID unknown at code-mint time; filled when the desktop POSTs (icon flow
    # sets it up front from Account-V1).
    puuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Icon flow only — single-use challenge nonce (UNIQUE ignores NULLs in PG).
    nonce: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # Icon flow only — the profile icon the user must set.
    target_icon_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Desktop-code flow only — the one-time link credential (stored de-hyphenated).
    # String(12) matches migration 0004 VARCHAR(12) (keeps autogenerate clean).
    code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Which verification flow this row belongs to (worker poll skips code rows).
    # String(16) matches migration 0004 VARCHAR(16) (keeps autogenerate clean).
    verify_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="icon")
    # Desktop-code flow — the guild the user ran /register from (plain FK; global).
    origin_guild_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
        PGUUID(as_uuid=True),
        ForeignKey("guilds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
    "user_guild_roles",
    "teams",
    "team_members",
    "tournaments",
    "tournament_participants",
    "tournament_hides",
    "registrations",
    "matches",
    "match_lobbies",
    "client_sessions",
    "result_reports",
    "match_results",
    "result_nonces",
)
