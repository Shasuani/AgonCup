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


class TeamMemberStatus(StrEnum):
    """Membership lifecycle (PLAN.md §3.1 team-leader approval flow).

    A player redeems a team join code → a ``pending`` membership request is created
    and surfaced to the captain, who ``active``-ates it (approve) or it is removed
    (reject). The captain's own membership is ``active`` from creation. The active
    roster is ``status == active``; the captain's pending queue is ``status == pending``.
    """

    PENDING = "pending"
    ACTIVE = "active"


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


class GameMode(StrEnum):
    """The custom-game shape a tournament runs (drives lobby config + quorum).

    ``SUMMONERS_RIFT_5V5`` is the default 5v5 on the Rift (mapId 11); ``ARAM_1V1``
    is a solo duel on the Howling Abyss (mapId 12, teamSize 1), decided by first
    blood. The mode threads Tournament -> Match -> lobby spec + corroboration.
    """

    SUMMONERS_RIFT_5V5 = "summoners_rift_5v5"
    ARAM_1V1 = "aram_1v1"

    @property
    def team_size(self) -> int:
        """Players per side for the LCU custom lobby."""
        return 1 if self is GameMode.ARAM_1V1 else 5

    @property
    def total_participants(self) -> int:
        """Expected total reporters/players in a match of this mode (2 or 10)."""
        return 2 if self is GameMode.ARAM_1V1 else 10

    @property
    def quorum_threshold(self) -> int:
        """Agreeing-report floor to auto-decide a game.

        Owner rule (2026-08-25): ONE report decides a 1v1 duel; a 5v5 needs TWO
        agreeing reports, one from each team (see :attr:`requires_both_teams`).
        Reports are forgeable — the low floor trades corroboration for autonomy,
        with admin override as the backstop (PLAN.md §3.6.6).
        """
        return 1 if self is GameMode.ARAM_1V1 else 2

    @property
    def requires_both_teams(self) -> bool:
        """Whether the agreeing reports must include BOTH rosters.

        True for the 5v5 (one report from each side); False for the 1v1 duel,
        where a single duelist's report is enough to decide the game.
        """
        return self is not GameMode.ARAM_1V1

    @property
    def decides_on_first_blood(self) -> bool:
        """Whether a game of this mode is won at first blood, not at the nexus.

        A 1v1 ARAM duel is decided by first blood (see the class docstring): the
        winner vote of each report derives from the corroborated ``:2999`` kill
        stream (PLAN.md §3.6.4) — the eog ``isWinningTeam`` bit is only the
        fallback, since duelists are expected to leave after the deciding kill.
        """
        return self is GameMode.ARAM_1V1


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
