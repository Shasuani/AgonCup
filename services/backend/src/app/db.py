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

Phase 1 status: tenant resolution is now REQUEST-AWARE and authenticated. The GUC
is set by :func:`app.auth.deps.get_tenant_session`, which derives the acting guild
from the request (path/header/JWT claim) and validates the principal's membership
before yielding the session. The Phase-0 ``_extract_guild_id`` sentinel stub is
gone; routes depend on ``get_tenant_session`` instead. The engine/sessionmaker
below remain the single shared connection source for both the request path and
the worker.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


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

# NOTE: the request-time tenant-scoped session dependency lives in
# ``app.auth.deps.get_tenant_session`` (it needs the authenticated principal to
# resolve + authorize the acting guild). It uses ``SessionLocal`` above and is the
# ONLY sanctioned way to obtain a guild-scoped session on the request path —
# every guild-scoped query MUST go through it so ``SET LOCAL app.current_guild``
# is applied inside the same transaction that runs the query.
