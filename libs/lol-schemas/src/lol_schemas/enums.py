"""Cross-cutting enumerations used by the canonical DTOs.

These mirror the string-valued status/state columns described in PLAN.md §2.
Kept here (rather than in ``lol_core``) so the wire DTOs are self-contained and
``lol_schemas`` carries no dependency on the domain-logic package.
"""

from __future__ import annotations

from enum import StrEnum


class GuildStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class TeamMemberRole(StrEnum):
    CAPTAIN = "captain"
    PLAYER = "player"
    SUB = "sub"


class UserGuildRoleName(StrEnum):
    """Per-(user, guild) authorization role (PLAN.md §3.1 auth, §11 decision 6).

    Ordered by privilege for RBAC checks: ``platform_operator`` is cross-tenant
    staff; ``guild_admin`` administers a single guild; ``captain`` manages a team;
    ``player`` is a baseline participant. Stored on ``user_guild_roles`` (the role
    lookup is naturally scoped to the current guild and lands under guild RLS).
    """

    PLATFORM_OPERATOR = "platform_operator"
    GUILD_ADMIN = "guild_admin"
    CAPTAIN = "captain"
    PLAYER = "player"


class TournamentStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    CHECK_IN = "check_in"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TournamentFormat(StrEnum):
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"


class RegistrationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHECKED_IN = "checked_in"


class MatchStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WALKOVER = "walkover"
    FORFEIT = "forfeit"


class LobbyPhase(StrEnum):
    """Phase-gated lobby state machine (PLAN.md §3.6.1).

    The ordered ``ASSIGNED_HOST -> ... -> RESULT_PENDING`` happy path; terminal
    ``ENDED``/``ABORTED``/``HOST_LOST`` states live alongside it.
    """

    ASSIGNED_HOST = "assigned_host"
    AWAITING_CREATE = "awaiting_create"
    LOBBY_OPEN = "lobby_open"
    FILLING = "filling"
    READY = "ready"
    STARTING = "starting"
    IN_GAME = "in_game"
    RESULT_PENDING = "result_pending"
    ENDED = "ended"
    ABORTED = "aborted"
    HOST_LOST = "host_lost"


class ResultSource(StrEnum):
    """Where a single participant's report was captured from (PLAN.md §3.6.5/6)."""

    EOG_STATS_BLOCK = "eog_stats_block"
    LIVE_CLIENT_DATA = "live_client_data"


class ResultDecidedBy(StrEnum):
    QUORUM = "quorum"
    ADMIN_OVERRIDE = "admin_override"
    WALKOVER = "walkover"
    FORFEIT = "forfeit"


class MatchResultState(StrEnum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"


class RiotRegion(StrEnum):
    """Continental routing clusters used for Account-V1 (PLAN.md §3.5)."""

    AMERICAS = "americas"
    ASIA = "asia"
    EUROPE = "europe"
