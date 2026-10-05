"""Team listings against a real Postgres, under FORCE RLS as ``app_rw`` (PLAN.md §3.1).

The offline suite (``test_team_listings.py``) simulates the policies; this one
runs the service on the non-BYPASSRLS application role, so it proves what a fake
cannot: a player whose client acts on server B really sees a team that lives on
server A, really lands a pending request there, and the captain's approval of
the fifth player really takes the post down — while a plain read from B still
sees none of A's teams.

Enable locally (mirrors ``tests/test_integration_pg.py``):

    LOL_DB_TESTS=1 \\
    OWNER_DATABASE_URL=postgresql+asyncpg://lol:lol@localhost:5432/lol \\
    APP_DATABASE_URL=postgresql+asyncpg://app_rw:app_rw@localhost:5432/lol \\
    pytest tests/test_team_listings_pg.py
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.auth.tenancy import set_acting_guild
from app.team_listings import service
from app.teams import service as teams
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
async def _clean_seed_rows() -> AsyncIterator[None]:
    await clean_seed_rows(OWNER_URL)
    yield
    await clean_seed_rows(OWNER_URL)


async def _insert(conn: AsyncConnection, sql: str, **params: object) -> uuid.UUID:
    return (await conn.execute(text(sql + " RETURNING id"), params)).scalar_one()  # type: ignore[no-any-return]


async def _user(conn: AsyncConnection, discord_id: int, name: str) -> uuid.UUID:
    return await _insert(
        conn,
        "INSERT INTO users (discord_user_id, display_name) VALUES (:d, :n)",
        d=discord_id,
        n=name,
    )


async def _seed(active_players: int) -> dict[str, uuid.UUID]:
    """Server A: a team (captain + ``active_players``). Server B: the stranger."""
    engine = create_async_engine(OWNER_URL)
    try:
        async with engine.begin() as conn:
            a = await _insert(
                conn, "INSERT INTO guilds (discord_guild_id, name) VALUES (961001, 'Server A')"
            )
            b = await _insert(
                conn, "INSERT INTO guilds (discord_guild_id, name) VALUES (961002, 'Server B')"
            )
            captain = await _user(conn, 961101, "Cap")
            stranger = await _user(conn, 961102, "Stranger")
            for uid, gid in ((captain, a), (stranger, b)):
                await conn.execute(
                    text(
                        "INSERT INTO user_guild_roles (user_id, guild_id, role) "
                        "VALUES (:u, :g, 'player')"
                    ),
                    {"u": uid, "g": gid},
                )
            for uid, tier in ((captain, "GOLD"), (stranger, None)):
                await conn.execute(
                    text(
                        "INSERT INTO riot_accounts (user_id, puuid, region, verified, rank_tier,"
                        " rank_division, rank_lp) VALUES (:u, :p, 'euw1', true, :t, 'II', 45)"
                    ),
                    {"u": uid, "p": f"pg-{uid}", "t": tier},
                )
            team = await _insert(
                conn,
                "INSERT INTO teams (name, guild_id, captain_user_id, join_code)"
                " VALUES ('Blue Wolves', :g, :u, 'PGWOLF23')",
                g=a,
                u=captain,
            )
            members = [captain]
            for i in range(active_players):
                members.append(await _user(conn, 961200 + i, f"P{i}"))
            for i, uid in enumerate(members):
                await conn.execute(
                    text(
                        "INSERT INTO team_members (team_id, user_id, guild_id, role, status)"
                        " VALUES (:t, :u, :g, :r, 'active')"
                    ),
                    {"t": team, "u": uid, "g": a, "r": "captain" if i == 0 else "player"},
                )
        return {"a": a, "b": b, "captain": captain, "stranger": stranger, "team": team}
    finally:
        await engine.dispose()


async def _owner_rows(sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(OWNER_URL)
    try:
        async with engine.connect() as conn:
            return [tuple(r) for r in (await conn.execute(text(sql), params)).all()]
    finally:
        await engine.dispose()


@pytest.fixture
async def app_engine() -> AsyncIterator[object]:
    engine = create_async_engine(APP_URL)
    yield engine
    await engine.dispose()


async def test_find_a_team_across_servers_under_real_rls(app_engine: object) -> None:
    ids = await _seed(active_players=3)  # 4 active: one slot left

    # The captain posts, acting on the team's server (A).
    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["a"]))
        listing = await service.create_listing(
            s, team_id=ids["team"], user_id=ids["captain"], region="euw1", message="need adc"
        )
        listing_id = listing.id

    # The stranger's client acts on THEIR server (B): RLS hides A's teams …
    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["b"]))
        direct = (await s.execute(text("SELECT id FROM teams"))).all()
        assert direct == []
        # … yet the listing shows the team, read live from A.
        views = await service.list_listings(
            s, viewer_id=ids["stranger"], is_platform_operator=False
        )
        assert [v.team_name for v in views] == ["Blue Wolves"]
        assert views[0].server_name == "Server A"
        assert views[0].member_count == 4
        assert views[0].members[0].rank_text == "Gold II, 45 LP"
        # The walk was undone: still on B.
        gid = (await s.execute(text("SELECT current_setting('app.current_guild', true)"))).scalar()
        assert gid == str(ids["b"])

        result = await service.request_to_join(s, listing_id=listing_id, user_id=ids["stranger"])
        assert result.member.status == "pending"

    # The request sits on A, and the stranger now holds a player role there.
    assert await _owner_rows(
        "SELECT status FROM team_members WHERE user_id = :u AND guild_id = :g",
        u=ids["stranger"],
        g=ids["a"],
    ) == [("pending",)]
    assert await _owner_rows(
        "SELECT role FROM user_guild_roles WHERE user_id = :u AND guild_id = :g",
        u=ids["stranger"],
        g=ids["a"],
    ) == [("player",)]

    # The captain approves the fifth player on A: the roster is full, the post goes.
    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["a"]))
        await teams.approve_join_request(s, team_id=ids["team"], user_id=ids["stranger"])

    assert await _owner_rows("SELECT id FROM team_listings WHERE id = :i", i=listing_id) == []


async def test_only_the_captain_takes_the_post_down_and_disband_removes_it(
    app_engine: object,
) -> None:
    ids = await _seed(active_players=1)
    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["a"]))
        listing = await service.create_listing(
            s, team_id=ids["team"], user_id=ids["captain"], region="euw1", message=None
        )
        listing_id = listing.id

    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["b"]))
        with pytest.raises(service.NotTeamCaptainError):
            await service.delete_listing(
                s, listing_id=listing_id, user_id=ids["stranger"], is_platform_operator=False
            )

    # Disbanding the team drops its post with it (composite FK, ON DELETE CASCADE).
    async with AsyncSession(app_engine) as s, s.begin():  # type: ignore[arg-type]
        await set_acting_guild(s, str(ids["a"]))
        await teams.delete_team(s, team_id=ids["team"])

    assert await _owner_rows("SELECT id FROM team_listings WHERE id = :i", i=listing_id) == []
