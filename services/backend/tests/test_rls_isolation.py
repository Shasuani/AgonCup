"""Cross-guild RLS isolation matrix (PLAN.md §7 Phase 1, §9).

Integration test — requires a live Postgres with migrations applied and the
``app_rw`` role created (migration 0002). It is **skipped** unless explicitly
enabled, so the unit suite stays offline; **CI runs it** against the compose
``postgres`` service.

Enable locally:

    LOL_DB_TESTS=1 \
    OWNER_DATABASE_URL=postgresql+asyncpg://lol:lol@localhost:5432/lol \
    APP_DATABASE_URL=postgresql+asyncpg://app_rw:app_rw@localhost:5432/lol \
    pytest tests/test_rls_isolation.py

What it proves:
  * a session scoped to guild A sees ONLY guild A rows (and vice-versa);
  * an UNSET ``app.current_guild`` GUC yields ZERO rows (fail closed);
  * reusing the same pooled connection across tenants does NOT leak (SET LOCAL is
    transaction-scoped) — the "RLS + pooled-connection leak" risk.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.dbseed import clean_seed_rows

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("LOL_DB_TESTS") != "1",
        reason="DB integration test; set LOL_DB_TESTS=1 (CI runs it).",
    ),
    pytest.mark.asyncio,
]

OWNER_URL = os.environ.get("OWNER_DATABASE_URL", "postgresql+asyncpg://lol:lol@localhost:5432/lol")
APP_URL = os.environ.get(
    "APP_DATABASE_URL", "postgresql+asyncpg://app_rw:app_rw@localhost:5432/lol"
)


@pytest.fixture(autouse=True)
async def _clean_seed_rows() -> None:
    """Make the committed seeds re-runnable against a persistent dev database."""
    await clean_seed_rows(OWNER_URL)


async def _make_guild_with_team(owner_conn, discord_id: int) -> tuple[uuid.UUID, uuid.UUID]:
    """As the owner (bypasses RLS), seed a guild + a user + a team in it."""
    guild_id = (
        await owner_conn.execute(
            text("INSERT INTO guilds (discord_guild_id, name) VALUES (:d, :n) RETURNING id"),
            {"d": discord_id, "n": f"guild-{discord_id}"},
        )
    ).scalar_one()
    user_id = (
        await owner_conn.execute(
            text("INSERT INTO users (discord_user_id, display_name) VALUES (:d, :n) RETURNING id"),
            {"d": discord_id, "n": f"cap-{discord_id}"},
        )
    ).scalar_one()
    await owner_conn.execute(
        text("INSERT INTO teams (name, guild_id, captain_user_id) VALUES (:n, :g, :u)"),
        {"n": f"team-{discord_id}", "g": guild_id, "u": user_id},
    )
    return guild_id, user_id


async def _count_teams(conn, guild_id: uuid.UUID | None) -> int:
    if guild_id is not None:
        await conn.execute(
            text("SELECT set_config('app.current_guild', :g, true)"), {"g": str(guild_id)}
        )
    return (await conn.execute(text("SELECT count(*) FROM teams"))).scalar_one()


async def test_cross_guild_isolation_and_fail_closed() -> None:
    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a, _ = await _make_guild_with_team(owner, 900001)
            g_b, _ = await _make_guild_with_team(owner, 900002)

        # Each tenant sees only its own team.
        async with app_engine.connect() as conn:
            async with conn.begin():
                assert await _count_teams(conn, g_a) == 1
            async with conn.begin():
                assert await _count_teams(conn, g_b) == 1
            # Fail closed: GUC unset in a fresh transaction -> no rows.
            async with conn.begin():
                assert await _count_teams(conn, None) == 0

        # Pooled-connection no-leak: same physical connection, switch tenants.
        async with app_engine.connect() as conn:
            async with conn.begin():
                a_seen = await _count_teams(conn, g_a)
            async with conn.begin():
                b_seen = await _count_teams(conn, g_b)
            assert a_seen == 1 and b_seen == 1  # neither leaked the other's rows
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()
