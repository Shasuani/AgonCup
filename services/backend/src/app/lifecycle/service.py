"""Guild + user lifecycle service (PLAN.md §3.2 multi-guild, §7 Phase 1 PII).

This is the data-deletion / purge core behind the lifecycle routes. It is kept as
a set of plain ``async`` functions over an :class:`~sqlalchemy.ext.asyncio.AsyncSession`
so it can be unit-tested against a fake/mock session and reused from both the
HTTP routes and (later) a worker job.

Soft vs hard deletion (PLAN.md §3.2 "Tenant lifecycle", §2 data model)
----------------------------------------------------------------------
* **Guild purge** — the :class:`~app.models.Guild` *anchor row* is **soft**-deleted:
  ``status`` flips to ``removed`` and ``removed_at`` is stamped. Its **guild-scoped
  data is HARD-deleted**: every child table FKs ``guilds.id`` with
  ``ON DELETE CASCADE`` (directly or transitively), so we issue a single
  ``DELETE`` against the guild-scoped tables and Postgres cascades the rest. We do
  NOT delete the anchor so re-invite within the retention window can reactivate it
  (PLAN.md §3.2) and the ``audit_log`` keeps a nullable-``guild_id`` reference.
  Global entities (``users``, ``riot_accounts``) are **never** touched by a guild
  purge — a person belongs to many guilds (PLAN.md §3.2 "Global entities are never
  hard-deleted").

* **User deletion** — the global :class:`~app.models.User` row is **HARD**-deleted.
  All global PII hanging off the user cascades via ``ON DELETE CASCADE`` on
  ``users.id``: ``riot_accounts``, ``oauth_credentials``, ``verification_attempts``,
  ``refresh_tokens``, ``user_guild_roles``, ``team_members``, ``client_sessions``.
  This is the "right to be forgotten" path (PLAN.md §7 Privacy/PII).

RLS / tenancy
-------------
:func:`purge_guild` issues the scoped ``DELETE``s **without** a tenant GUC by
deleting on the explicit ``guild_id`` predicate, and is only reachable behind the
RBAC guard on the route (``guild_admin`` of that guild, or a ``platform_operator``).
The route sets ``app.current_guild`` for the acting guild first, so the deletes run
inside the same RLS transaction. :func:`delete_user` operates on GLOBAL tables
(no RLS), guarded at the route by self-or-platform-operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from lol_schemas.enums import GuildStatus
from sqlalchemy import CursorResult, Delete, Update, delete, update
from sqlalchemy.exc import IntegrityError

from app.auth import tokens
from app.logging import get_logger
from app.models import (
    GUILD_ISOLATED_TABLES,
    Guild,
    User,
)
from app.models.tables import (
    ClientSession,
    Match,
    MatchLobby,
    MatchResult,
    Registration,
    ResultNonce,
    ResultReport,
    Team,
    TeamMember,
    Tournament,
    TournamentHide,
    TournamentParticipant,
    UserGuildRole,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("app.lifecycle")

# Guild-scoped tables to hard-delete on purge, ordered children-before-parents so
# the explicit DELETEs succeed even where a same-guild composite FK lacks a cascade
# path (defense in depth — the DB cascade from ``guilds.id`` would also handle it).
# Mirrors models.GUILD_ISOLATED_TABLES; asserted in sync below.
# Typed ``Any`` because these mapped classes do not share a single statically
# known base exposing both ``__tablename__`` and the ``guild_id`` mapped column
# (``TeamMember`` denormalizes ``guild_id`` without mixing in ``GuildScoped``).
_PURGE_ORDER: tuple[Any, ...] = (
    ResultNonce,
    ResultReport,
    MatchResult,
    MatchLobby,
    Match,
    Registration,
    TournamentHide,
    TournamentParticipant,
    Tournament,
    TeamMember,
    Team,
    ClientSession,
    UserGuildRole,
)

# Fail loud at import if a new guild-scoped table is added to the model layer but
# not wired into the purge order (so a future table can't silently survive a purge).
assert {m.__tablename__ for m in _PURGE_ORDER} == set(GUILD_ISOLATED_TABLES), (
    "lifecycle purge order is out of sync with GUILD_ISOLATED_TABLES: "
    f"{sorted({m.__tablename__ for m in _PURGE_ORDER})} != {sorted(GUILD_ISOLATED_TABLES)}"
)


async def _dml(session: AsyncSession, statement: Update | Delete) -> CursorResult[Any]:
    """Run a DML statement and return the cursor result (exposes ``rowcount``)."""
    return cast("CursorResult[Any]", await session.execute(statement))


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Outcome of a guild purge."""

    guild_id: str
    status: str
    removed_at: datetime
    deleted_rows: dict[str, int]

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted_rows.values())


@dataclass(frozen=True, slots=True)
class UserDeletionResult:
    """Outcome of a user PII deletion."""

    user_id: str
    deleted: bool


class GuildNotFoundError(LookupError):
    """Raised when a purge targets a guild that does not exist."""


class UserNotFoundError(LookupError):
    """Raised when a deletion targets a user that does not exist."""


class UserStillCaptainError(RuntimeError):
    """Raised when PII deletion is blocked because the user still captains teams.

    ``teams.captain_user_id`` deliberately does NOT cascade (a team must keep a
    captain), and the pre-check cannot be a SELECT: ``teams`` is under guild RLS
    and this global path runs with no tenant GUC, so the rows are invisible here.
    The database's FK rejection IS the check; this error is its domain shape.
    """


async def purge_guild(
    session: AsyncSession,
    *,
    guild_id: str,
    now: datetime | None = None,
) -> PurgeResult:
    """Soft-delete the guild anchor + HARD-delete its guild-scoped data.

    Marks the :class:`~app.models.Guild` row ``status='removed'`` with
    ``removed_at`` stamped (soft), then deletes every guild-scoped table row for
    that guild (hard). The DB also cascades from ``guilds.id``; the explicit
    ordered deletes here make the behaviour testable against a fake session and
    independent of FK-cascade configuration. See module docstring for the soft/hard
    matrix (PLAN.md §3.2 / §7).

    Raises:
        GuildNotFoundError: no guild row matches ``guild_id``.
    """
    stamp = now or datetime.now(tz=UTC)

    result = await _dml(
        session,
        update(Guild)
        .where(Guild.id == guild_id)
        .values(status=GuildStatus.REMOVED.value, removed_at=stamp),
    )
    if (result.rowcount or 0) == 0:
        raise GuildNotFoundError(guild_id)

    deleted_rows: dict[str, int] = {}
    for model in _PURGE_ORDER:
        res = await _dml(session, delete(model).where(model.guild_id == guild_id))
        deleted_rows[model.__tablename__] = int(res.rowcount or 0)

    purge = PurgeResult(
        guild_id=guild_id,
        status=GuildStatus.REMOVED.value,
        removed_at=stamp,
        deleted_rows=deleted_rows,
    )
    log.info(
        "guild_purged",
        guild_id=guild_id,
        total_deleted=purge.total_deleted,
        deleted_rows=deleted_rows,
    )
    return purge


async def purge_guild_by_discord_id(
    session: AsyncSession,
    *,
    discord_guild_id: int,
    now: datetime | None = None,
) -> PurgeResult:
    """Resolve a guild by its Discord snowflake, then :func:`purge_guild` it.

    The Discord bot's ``on_guild_remove`` only knows the Discord snowflake, not our
    internal UUID, so this is the entry point for the service-auth purge route.

    Raises:
        GuildNotFoundError: no guild row matches ``discord_guild_id``.
    """
    guild_id = (
        await session.execute(
            update(Guild)
            .where(Guild.discord_guild_id == discord_guild_id)
            .values(
                status=GuildStatus.REMOVED.value,
                removed_at=now or datetime.now(tz=UTC),
            )
            .returning(Guild.id)
        )
    ).scalar_one_or_none()
    if guild_id is None:
        raise GuildNotFoundError(str(discord_guild_id))

    # The UPDATE above already soft-deleted the anchor; reuse purge_guild for the
    # scoped hard-deletes (its own UPDATE is idempotent — same values).
    return await purge_guild(session, guild_id=str(guild_id), now=now)


async def delete_user(
    session: AsyncSession,
    *,
    user_id: str,
) -> UserDeletionResult:
    """HARD-delete a user and all global PII cascading off ``users.id``.

    Deletes the :class:`~app.models.User` row; ``riot_accounts``,
    ``oauth_credentials``, ``verification_attempts``, ``refresh_tokens``,
    ``user_guild_roles``, ``team_members`` and ``client_sessions`` all carry
    ``ON DELETE CASCADE`` FKs to ``users.id`` and are removed by the database. This
    is the user "right to be forgotten" path (PLAN.md §7 Privacy/PII).

    Refresh tokens are revoked EXPLICITLY first (``revoke_all_for_user``) so any
    token presented in the narrow window before the cascading DELETE removes the
    rows fails closed — the ``users.id`` CASCADE would remove them anyway, but a
    leaked session must not survive a deletion that has begun. (Contrast
    :func:`purge_guild`, which does NOT revoke refresh tokens: they are global /
    identity-scoped, so a user removed from one guild stays signed in elsewhere;
    stale access JWTs for the removed guild fail closed at RLS.)

    Raises:
        UserNotFoundError: no user row matches ``user_id``.
        UserStillCaptainError: the user still captains a team (RESTRICT FK).
    """
    # Revoke the user's refresh tokens BEFORE the cascading DELETE (defense in depth).
    await tokens.revoke_all_for_user(session, user_id=user_id)

    try:
        result = await _dml(session, delete(User).where(User.id == user_id))
    except IntegrityError as exc:
        if "fk_teams_captain_user_id_users" in str(exc.orig):
            raise UserStillCaptainError(user_id) from exc
        raise

    if (result.rowcount or 0) == 0:
        raise UserNotFoundError(user_id)

    log.info("user_pii_deleted", user_id=user_id)
    return UserDeletionResult(user_id=user_id, deleted=True)
