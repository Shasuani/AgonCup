"""Async SQLAlchemy engine/session + the per-request RLS GUC dependency.

RLS DISCIPLINE (PLAN.md §1 / §2 — multi-tenant isolation)
---------------------------------------------------------
All guild-scoped tables are protected by Postgres Row-Level Security (FORCE RLS,
non-BYPASSRLS app role). The tenant is selected by a per-request session GUC:

    SET LOCAL app.current_guild = :gid

``SET LOCAL`` scopes the value to the *current transaction* only, which is what
makes this safe under a pooled connection (e.g. pgbouncer): the GUC cannot bleed
into the next request that reuses the connection (PLAN.md §9 — "RLS + pooled-
connection leak"). The RLS policies themselves (``current_setting('app.current_
guild')``) and the non-BYPASSRLS role land with the first guild-scoped migration
in Phase 1; this module establishes the *discipline* now so every request path is
already shaped correctly.

Phase 0 status: guild extraction is STUBBED (no auth wired yet). The dependency
opens a transaction, sets the GUC, yields the session, and commits/rolls back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# A sentinel guild used until auth/tenant resolution is implemented (Phase 1).
# RLS policies treat this as "no tenant" so a misconfigured request fails closed
# rather than leaking another guild's rows.
_STUB_GUILD_ID = "00000000-0000-0000-0000-000000000000"


def _make_engine() -> AsyncEngine:
    settings = get_settings()
    # The request path connects as the non-BYPASSRLS app_rw role
    # (request_database_url) so FORCE-RLS guild policies bind. Migrations use the
    # owner DSN (database_url) separately via Alembic. See PLAN.md §9.
    return create_async_engine(
        settings.request_database_url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )


# Module-level engine/sessionmaker. Created lazily-ish at import; for tests that
# never touch the DB this is cheap (no connection is opened until first use).
engine: AsyncEngine = _make_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


def _extract_guild_id() -> str:
    """STUB tenant extraction.

    Phase 1 replaces this with the authenticated ``(user, guild)`` resolution
    (JWT claim + Discord role re-verification, PLAN.md §3.1 auth/). For now it
    returns a sentinel so the GUC discipline is exercised end-to-end.
    """
    return _STUB_GUILD_ID


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a transaction-scoped session with the RLS GUC set.

    Every guild-scoped query MUST go through this dependency so that
    ``SET LOCAL app.current_guild`` is applied inside the same transaction that
    runs the query. Do not share sessions across requests.
    """
    guild_id = _extract_guild_id()
    async with SessionLocal() as session, session.begin():
        # SET LOCAL with a bound parameter is not allowed by Postgres, so we
        # use set_config(..., is_local => true), which is the parameterized
        # equivalent of `SET LOCAL` and is injection-safe.
        await session.execute(
            text("SELECT set_config('app.current_guild', :gid, true)"),
            {"gid": guild_id},
        )
        yield session
