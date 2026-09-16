"""Walking the caller's OWN guilds inside one tenant transaction (PLAN.md §3.1).

The desktop client has no server picker: it speaks for one user, who may hold a
role in several Discord servers. :func:`app.auth.deps.get_tenant_session` pins a
request to a single acting guild (``X-Guild-Id`` or the JWT default — the guild
where the user holds their most privileged role), and under FORCE RLS every other
guild's rows are invisible for that transaction. So a join code minted on server B
read as "invalid" whenever the client's default guild was A, and every ``/mine``
list only ever showed one server's rows.

These helpers let a *self-scoped* read visit every guild the user belongs to, in
the same transaction, by re-pointing the ``app.current_guild`` GUC guild by guild.
They do not widen what a user can see: the guild list is their own
``user_guild_roles`` rows (readable across guilds through the ``self_role_read``
policy keyed on ``app.current_user``), so each step runs exactly the query the
tenant session would have run had the client sent that guild's id — a server the
user is not a member of is never visited, and the ``guild_isolation`` policy still
binds inside every step.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserGuildRole


async def current_guild_id(session: AsyncSession) -> str | None:
    """The acting guild on this transaction (``app.current_guild``), or None if unset."""
    gid = (
        await session.execute(text("SELECT current_setting('app.current_guild', true)"))
    ).scalar_one_or_none()
    return str(gid) if gid else None


async def set_acting_guild(session: AsyncSession, guild_id: str) -> None:
    """Re-point ``app.current_guild`` for the rest of the transaction (``is_local``)."""
    await session.execute(
        text("SELECT set_config('app.current_guild', :gid, true)"),
        {"gid": guild_id},
    )


async def guild_ids_for_user(session: AsyncSession, *, user_id: uuid.UUID | str) -> list[str]:
    """Every guild the user holds a role in, acting guild first, deduplicated.

    Reads the user's own ``user_guild_roles`` rows across guilds via the
    ``self_role_read`` policy (``app.current_user``), the same way login/refresh
    pick the JWT's default guild. The acting guild leads so a caller that already
    sits on the right tenant does not hop away and back for nothing.
    """
    await session.execute(
        text("SELECT set_config('app.current_user', :uid, true)"),
        {"uid": str(user_id)},
    )
    rows = (
        await session.execute(
            select(UserGuildRole.guild_id).where(UserGuildRole.user_id == user_id)
        )
    ).all()
    acting = await current_guild_id(session)
    ordered: list[str] = [acting] if acting else []
    for (gid,) in rows:
        value = str(gid)
        if value not in ordered:
            ordered.append(value)
    return ordered


async def across_user_guilds[T](
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    read: Callable[[], Awaitable[list[T]]],
) -> list[T]:
    """Run a self-scoped list ``read`` once per guild the user belongs to; concatenate.

    Acting guild first, then the rest; the acting guild is restored afterwards so
    the caller's transaction ends on the tenant it started on. Rows keep their
    per-guild order — a client that needs one global order sorts on a row field.
    With no guild to visit at all (no acting guild, no role rows) the read runs
    once on the session as it is, which under RLS fails closed as before.
    """
    origin = await current_guild_id(session)
    guilds = await guild_ids_for_user(session, user_id=user_id)
    if not guilds:
        return await read()
    out: list[T] = []
    try:
        for guild_id in guilds:
            await set_acting_guild(session, guild_id)
            out.extend(await read())
    finally:
        if origin is not None:
            await set_acting_guild(session, origin)
    return out


class GuildOwned(Protocol):
    """A guild-scoped row: the one attribute the tenant walk needs to read back."""

    @property
    def guild_id(self) -> uuid.UUID: ...


async def find_across_user_guilds[T: GuildOwned](
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    find: Callable[[], Awaitable[T | None]],
) -> T | None:
    """Try a self-scoped lookup guild by guild until it returns a row.

    On a hit the transaction is LEFT pointing at the guild that owns the row —
    the write that usually follows (redeem a join code → insert an enrolment) must
    land in that same tenant, and guild-scoped inserts read ``guild_id`` from the
    GUC. On a miss the acting guild is restored and ``None`` is returned, so a row
    in a server the user is not a member of stays exactly as invisible as before.

    The row's own ``guild_id`` is what the GUC is set to, and a row owned by a
    guild the user does not belong to counts as a miss. Under RLS both are
    already true of any row ``find`` can return; this makes them true without
    RLS too (a misconfigured dev backend on the owner DSN sees every guild's
    rows, and would otherwise resolve the first guild's code and enrol into
    the wrong tenant).
    """
    origin = await current_guild_id(session)
    guilds = await guild_ids_for_user(session, user_id=user_id)
    if not guilds:
        return await find()
    for guild_id in guilds:
        await set_acting_guild(session, guild_id)
        found = await find()
        if found is None:
            continue
        owner = str(found.guild_id)
        if owner not in guilds:
            break
        if owner != guild_id:
            await set_acting_guild(session, owner)
        return found
    if origin is not None:
        await set_acting_guild(session, origin)
    return None
