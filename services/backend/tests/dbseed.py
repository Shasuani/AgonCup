"""Shared cleanup for the Postgres-backed integration tests.

Both DB suites (``test_rls_isolation.py``, ``test_integration_pg.py``) seed their
tenants over the OWNER connection inside ``engine.begin()`` — which COMMITS, so
the rows outlive the test. The seeded Discord snowflakes are hardcoded (the
assertions read display names like ``admin-950001`` back), so they cannot simply
be randomized per run.

CI never noticed: its Postgres is a throwaway service container, fresh on every
run. Against a persistent dev database the SECOND run dies on
``uq_guilds_discord_guild_id`` — a failure that reads like an RLS regression and
is nothing of the sort. Wiping the range these suites own before each test makes
a local run behave like CI's fresh database.

Not a ``conftest.py`` autouse fixture on purpose: that would fire for the whole
offline unit suite too, forcing every test to reach for a Postgres that the
`LOL_DB_TESTS` gate exists precisely to avoid needing.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

#: The Discord snowflake range the DB suites own. Every seeded guild/user id
#: lives here; nothing outside it is ever touched.
SEED_ID_MIN = 900000
SEED_ID_MAX = 999999


async def clean_seed_rows(owner_url: str) -> None:
    """Delete this range's guilds and users over the OWNER (BYPASSRLS) role.

    Deleting a guild cascades to its teams, tournaments, roles and members. The
    global ``users`` table carries no guild FK, so those rows go explicitly.
    """
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as conn:
            for table, column in (("guilds", "discord_guild_id"), ("users", "discord_user_id")):
                await conn.execute(
                    text(f"DELETE FROM {table} WHERE {column} BETWEEN :lo AND :hi"),  # noqa: S608
                    {"lo": SEED_ID_MIN, "hi": SEED_ID_MAX},
                )
    finally:
        await engine.dispose()
