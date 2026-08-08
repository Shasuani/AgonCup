"""Postgres-backed integration test: the self-read GUC + cross-guild escalation
(PLAN.md §3.1 auth, §7 Phase 1, §9).

This is the Phase-1 follow-up the security review demanded after
``tests/test_rls_isolation.py`` proved baseline per-guild isolation. Where that
test exercises the ``guild_isolation`` policy, this one exercises the auth layer
built on top of it against a **real** Postgres with the **non-BYPASSRLS**
``app_rw`` role under ``FORCE ROW LEVEL SECURITY``:

1. **The critical fix** — the ``self_role_read`` policy (migration 0003) keyed on
   the ``app.current_user`` GUC. ``app/auth/router._roles_for`` must be able to
   read a user's OWN ``user_guild_roles`` rows ACROSS guilds at login/refresh
   time, when no ``app.current_guild`` is set. We assert that the GUC unlocks
   exactly the own user's rows and that WITHOUT it the read returns ZERO rows
   (``guild_isolation`` alone fails closed for app_rw under FORCE RLS).
2. **Cross-guild escalation closed** — replicates the SQL that
   ``app/auth/deps.require_tenant_role`` runs under RLS: a ``guild_admin`` of
   guild A, when the acting tenant GUC is guild B, sees NONE of their A-scoped
   ``guild_admin`` rows, so the role check cannot be satisfied (the A-admin
   cannot pass ``require_tenant_role(GUILD_ADMIN)`` for guild B).
3. **Cross-guild row isolation** on ``tournaments`` and ``teams`` (defense in
   depth, mirroring ``_roles_for``'s reliance on FORCE RLS for everything else).

Enable locally (mirrors ``tests/test_rls_isolation.py``):

    LOL_DB_TESTS=1 \
    OWNER_DATABASE_URL=postgresql+asyncpg://lol:lol@localhost:5432/lol \
    APP_DATABASE_URL=postgresql+asyncpg://app_rw:app_rw@localhost:5432/lol \
    pytest tests/test_integration_pg.py

Requires a live Postgres with migrations 0001-0003 applied and the ``app_rw``
role created (migration 0002). Skipped unless ``LOL_DB_TESTS=1`` so the unit
suite stays offline; CI runs it against the compose ``postgres`` service.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import pytest
from lol_schemas.enums import UserGuildRoleName
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

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

GUILD_ADMIN = UserGuildRoleName.GUILD_ADMIN.value
PLAYER = UserGuildRoleName.PLAYER.value


@pytest.fixture(autouse=True)
async def _clean_seed_rows() -> None:
    """Make the committed seeds re-runnable against a persistent dev database."""
    await clean_seed_rows(OWNER_URL)


@dataclass(frozen=True, slots=True)
class SeededGuild:
    """A fully seeded tenant: a guild + an admin user with a role + a team + a
    tournament, all owner-inserted (the owner bypasses RLS)."""

    guild_id: uuid.UUID
    admin_user_id: uuid.UUID
    team_id: uuid.UUID
    tournament_id: uuid.UUID


async def _seed_guild(owner_conn: AsyncConnection, discord_id: int) -> SeededGuild:
    """Seed a complete guild over the OWNER connection (bypasses RLS).

    Creates: a guild, a user, that user's ``guild_admin`` ``user_guild_roles``
    row, a team captained by them, and a tournament — the rows the assertions
    read back as the non-BYPASSRLS ``app_rw`` role under FORCE RLS.
    """
    guild_id = (
        await owner_conn.execute(
            text("INSERT INTO guilds (discord_guild_id, name) VALUES (:d, :n) RETURNING id"),
            {"d": discord_id, "n": f"guild-{discord_id}"},
        )
    ).scalar_one()
    user_id = (
        await owner_conn.execute(
            text("INSERT INTO users (discord_user_id, display_name) VALUES (:d, :n) RETURNING id"),
            {"d": discord_id, "n": f"admin-{discord_id}"},
        )
    ).scalar_one()
    await owner_conn.execute(
        text("INSERT INTO user_guild_roles (user_id, guild_id, role) VALUES (:u, :g, :r)"),
        {"u": user_id, "g": guild_id, "r": GUILD_ADMIN},
    )
    team_id = (
        await owner_conn.execute(
            text(
                "INSERT INTO teams (name, guild_id, captain_user_id) "
                "VALUES (:n, :g, :u) RETURNING id"
            ),
            {"n": f"team-{discord_id}", "g": guild_id, "u": user_id},
        )
    ).scalar_one()
    tournament_id = (
        await owner_conn.execute(
            text("INSERT INTO tournaments (name, guild_id) VALUES (:n, :g) RETURNING id"),
            {"n": f"tournament-{discord_id}", "g": guild_id},
        )
    ).scalar_one()
    return SeededGuild(
        guild_id=guild_id,
        admin_user_id=user_id,
        team_id=team_id,
        tournament_id=tournament_id,
    )


async def _set_current_user(conn: AsyncConnection, user_id: uuid.UUID | None) -> None:
    """Set (or clear) the ``app.current_user`` GUC that ``self_role_read`` keys on.

    Mirrors ``app/auth/router._roles_for``: transaction-local ``set_config(...,
    true)``. Passing ``None`` clears it so the read falls back to
    ``guild_isolation`` alone (which fails closed for app_rw under FORCE RLS).
    """
    await conn.execute(
        text("SELECT set_config('app.current_user', :u, true)"),
        {"u": str(user_id) if user_id is not None else ""},
    )


async def _set_current_guild(conn: AsyncConnection, guild_id: uuid.UUID | None) -> None:
    """Set (or clear) the ``app.current_guild`` GUC RLS isolates tenants on.

    Mirrors ``app/auth/deps.get_tenant_session``: transaction-local.
    """
    await conn.execute(
        text("SELECT set_config('app.current_guild', :g, true)"),
        {"g": str(guild_id) if guild_id is not None else ""},
    )


async def _self_roles_across_guilds(
    conn: AsyncConnection, user_id: uuid.UUID
) -> list[tuple[uuid.UUID, str]]:
    """The ``_roles_for`` read: a user's OWN ``user_guild_roles`` across guilds.

    No ``app.current_guild`` is set, so this only returns rows if the
    ``self_role_read`` policy admits them via the ``app.current_user`` GUC.
    """
    rows = (
        await conn.execute(
            text("SELECT guild_id, role FROM user_guild_roles WHERE user_id = :u"),
            {"u": str(user_id)},
        )
    ).all()
    return [(gid, str(role)) for gid, role in rows]


async def _admin_roles_in_current_tenant(conn: AsyncConnection, user_id: uuid.UUID) -> set[str]:
    """The ``require_tenant_role`` read: roles the user holds in the CURRENTLY-SET
    acting guild only.

    Replicates ``app/auth/deps.require_tenant_role``'s inner query
    (``select(UserGuildRole.role).where(user_id == ...)``) which runs under RLS
    scoped to the already-set ``app.current_guild`` — so a role held in another
    guild cannot satisfy it. ``self_role_read`` is FOR SELECT and keyed on
    ``app.current_user`` (left UNSET here, as ``require_tenant_role`` does), so it
    does NOT widen this read.
    """
    rows = (
        await conn.execute(
            text("SELECT role FROM user_guild_roles WHERE user_id = :u"),
            {"u": str(user_id)},
        )
    ).all()
    return {str(role) for (role,) in rows}


async def test_self_role_read_guc_is_the_critical_fix() -> None:
    """(1) ``self_role_read``: the OWN-user cross-guild read works WITH the
    ``app.current_user`` GUC and returns ZERO rows WITHOUT it (FORCE RLS fail
    closed for app_rw) — the critical login/refresh fix (PLAN.md §3.1, §9)."""
    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 910001)
            g_b = await _seed_guild(owner, 910002)

        async with app_engine.connect() as conn:
            # WITH the GUC: the user reads their OWN role row(s) across guilds,
            # with NO app.current_guild set (exactly _roles_for's situation).
            async with conn.begin():
                await _set_current_user(conn, g_a.admin_user_id)
                rows = await _self_roles_across_guilds(conn, g_a.admin_user_id)
                assert rows == [(g_a.guild_id, GUILD_ADMIN)]

            # WITHOUT the GUC: guild_isolation alone, no app.current_guild set ->
            # ZERO rows. This is the failure mode the policy fixes.
            async with conn.begin():
                await _set_current_user(conn, None)
                rows = await _self_roles_across_guilds(conn, g_a.admin_user_id)
                assert rows == []

            # self_role_read only ever exposes the CURRENT user's own rows: with
            # the GUC set to user A, user B's rows stay invisible.
            async with conn.begin():
                await _set_current_user(conn, g_a.admin_user_id)
                rows = await _self_roles_across_guilds(conn, g_b.admin_user_id)
                assert rows == []
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()


async def test_guild_admin_a_cannot_pass_require_tenant_role_for_guild_b() -> None:
    """(2) Cross-guild escalation closed: a ``guild_admin`` of guild A, acting as
    tenant B, holds NO ``guild_admin`` row visible under B's RLS scope — so the
    ``require_tenant_role(GUILD_ADMIN)`` check (PLAN.md §3.1, §9) cannot pass.

    ``self_role_read`` does not rescue them: it is FOR SELECT keyed on
    ``app.current_user``, which ``require_tenant_role`` leaves unset; even if set,
    the guard's allow-decision is on roles visible in the acting guild, and here
    A's admin row is in guild A, not B.
    """
    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 920001)
            g_b = await _seed_guild(owner, 920002)

        async with app_engine.connect() as conn:
            # Sanity: as tenant A, A's admin DOES hold guild_admin (the guard
            # passes for their own guild).
            async with conn.begin():
                await _set_current_guild(conn, g_a.guild_id)
                held = await _admin_roles_in_current_tenant(conn, g_a.admin_user_id)
                assert GUILD_ADMIN in held

            # The escalation: A's admin acting as tenant B sees NO role rows ->
            # require_tenant_role(GUILD_ADMIN) for guild B is rejected.
            async with conn.begin():
                await _set_current_guild(conn, g_b.guild_id)
                held = await _admin_roles_in_current_tenant(conn, g_a.admin_user_id)
                assert GUILD_ADMIN not in held
                assert held == set()

            # Even with app.current_user set to themselves (self_role_read FOR
            # SELECT), the acting-tenant guard still must not see a B-scoped
            # guild_admin row — A's admin has none in B.
            async with conn.begin():
                await _set_current_guild(conn, g_b.guild_id)
                await _set_current_user(conn, g_a.admin_user_id)
                rows = (
                    await conn.execute(
                        text(
                            "SELECT role FROM user_guild_roles WHERE user_id = :u AND guild_id = :g"
                        ),
                        {"u": str(g_a.admin_user_id), "g": str(g_b.guild_id)},
                    )
                ).all()
                assert rows == []
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()


async def test_cross_guild_row_isolation_tournaments_and_teams() -> None:
    """(3) Cross-guild row isolation on ``tournaments`` and ``teams``: each tenant
    sees only its own rows, and an UNSET ``app.current_guild`` fails closed
    (PLAN.md §7 Phase 1, §9)."""
    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 930001)
            g_b = await _seed_guild(owner, 930002)

        async with app_engine.connect() as conn:
            # Tenant A: sees A's tournament + team, not B's.
            async with conn.begin():
                await _set_current_guild(conn, g_a.guild_id)
                tournaments = (
                    (await conn.execute(text("SELECT id FROM tournaments"))).scalars().all()
                )
                teams = (await conn.execute(text("SELECT id FROM teams"))).scalars().all()
                assert tournaments == [g_a.tournament_id]
                assert teams == [g_a.team_id]

            # Tenant B: the mirror image.
            async with conn.begin():
                await _set_current_guild(conn, g_b.guild_id)
                tournaments = (
                    (await conn.execute(text("SELECT id FROM tournaments"))).scalars().all()
                )
                teams = (await conn.execute(text("SELECT id FROM teams"))).scalars().all()
                assert tournaments == [g_b.tournament_id]
                assert teams == [g_b.team_id]

            # Fail closed: no app.current_guild set in a fresh transaction.
            async with conn.begin():
                t_count = (
                    await conn.execute(text("SELECT count(*) FROM tournaments"))
                ).scalar_one()
                team_count = (await conn.execute(text("SELECT count(*) FROM teams"))).scalar_one()
                assert t_count == 0
                assert team_count == 0
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()


async def test_cross_guild_row_isolation_tournament_participants() -> None:
    """(4) The new ``tournament_participants`` pool is under the same
    ``guild_isolation`` policy: a user enrolled in guild A's tournament is invisible
    to tenant B, and an UNSET ``app.current_guild`` fails closed (PLAN.md §3.1)."""
    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 940001)
            g_b = await _seed_guild(owner, 940002)
            # Enrol each guild's admin into its own tournament (over the owner conn).
            for g in (g_a, g_b):
                await owner.execute(
                    text(
                        "INSERT INTO tournament_participants "
                        "(tournament_id, user_id, guild_id) VALUES (:t, :u, :g)"
                    ),
                    {"t": g.tournament_id, "u": g.admin_user_id, "g": g.guild_id},
                )

        async with app_engine.connect() as conn:
            # Tenant A sees only A's participant row.
            async with conn.begin():
                await _set_current_guild(conn, g_a.guild_id)
                rows = (
                    (await conn.execute(text("SELECT tournament_id FROM tournament_participants")))
                    .scalars()
                    .all()
                )
                assert rows == [g_a.tournament_id]

            # Tenant B: the mirror image.
            async with conn.begin():
                await _set_current_guild(conn, g_b.guild_id)
                rows = (
                    (await conn.execute(text("SELECT tournament_id FROM tournament_participants")))
                    .scalars()
                    .all()
                )
                assert rows == [g_b.tournament_id]

            # Fail closed: no acting guild → zero rows.
            async with conn.begin():
                count = (
                    await conn.execute(text("SELECT count(*) FROM tournament_participants"))
                ).scalar_one()
                assert count == 0
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()


async def test_list_teams_overview_joins_captain_and_counts_under_rls() -> None:
    """``list_teams_overview`` resolves the captain's NAME and the roster size in
    one RLS-bound query (PLAN.md §3.1 teams).

    The listing previously returned ``Team`` alone, so the admin SPA could only
    show ``captain_user_id`` — a bare UUID. This covers the three things the new
    query has to get right and that a fake session cannot prove:

      1. the LEFT JOIN to the GLOBAL ``users`` table resolves under the
         non-BYPASSRLS ``app_rw`` role (``users`` carries no policy);
      2. the correlated ``team_members`` count is itself guild-isolated, so a
         same-id member row in another tenant cannot inflate it;
      3. ``is_solo`` scaffolding teams stay out of the listing.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.teams.service import list_teams_overview

    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 950001)
            g_b = await _seed_guild(owner, 950002)
            # Two members on A's team: the captain plus one player.
            player_id = (
                await owner.execute(
                    text(
                        "INSERT INTO users (discord_user_id, display_name) "
                        "VALUES (:d, :n) RETURNING id"
                    ),
                    {"d": 950003, "n": "Second Player"},
                )
            ).scalar_one()
            for uid, role in ((g_a.admin_user_id, "captain"), (player_id, "player")):
                await owner.execute(
                    text(
                        "INSERT INTO team_members (team_id, user_id, guild_id, role, status) "
                        "VALUES (:t, :u, :g, :r, 'active')"
                    ),
                    {"t": g_a.team_id, "u": uid, "g": g_a.guild_id, "r": role},
                )
            # A PENDING join request on A's team. It is NOT roster membership, so
            # it must not inflate the count: the listing would otherwise say 3
            # where the roster view, and the MAX_TEAM_SIZE cap, both say 2.
            pending_id = (
                await owner.execute(
                    text(
                        "INSERT INTO users (discord_user_id, display_name) "
                        "VALUES (:d, :n) RETURNING id"
                    ),
                    {"d": 950004, "n": "Hopeful Applicant"},
                )
            ).scalar_one()
            await owner.execute(
                text(
                    "INSERT INTO team_members (team_id, user_id, guild_id, role, status) "
                    "VALUES (:t, :u, :g, 'player', 'pending')"
                ),
                {"t": g_a.team_id, "u": pending_id, "g": g_a.guild_id},
            )
            # Guild B's own team gets a member too — it must not bleed into A.
            await owner.execute(
                text(
                    "INSERT INTO team_members (team_id, user_id, guild_id, role, status) "
                    "VALUES (:t, :u, :g, 'captain', 'active')"
                ),
                {"t": g_b.team_id, "u": g_b.admin_user_id, "g": g_b.guild_id},
            )
            # A solo scaffolding team in guild A: must be filtered out entirely.
            await owner.execute(
                text(
                    "INSERT INTO teams (name, guild_id, captain_user_id, is_solo) "
                    "VALUES ('solo-scaffold', :g, :u, true)"
                ),
                {"g": g_a.guild_id, "u": g_a.admin_user_id},
            )

        session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with session_factory() as session:
            await _set_current_guild(await session.connection(), g_a.guild_id)
            rows = await list_teams_overview(session)

            assert [r.team.id for r in rows] == [g_a.team_id], "solo team must be excluded"
            (row,) = rows
            assert row.captain_name == "admin-950001"
            assert row.member_count == 2, (
                "the count must be the ACTIVE roster only: guild B's member row "
                "must not bleed in, and A's PENDING request is not a member yet"
            )
            await session.rollback()
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()


async def test_discord_admin_role_sync_runs_under_rls() -> None:
    """(4) ``guilds.sync_discord_admin_roles`` against the real policies.

    The sync writes ``user_guild_roles`` as the non-BYPASSRLS ``app_rw`` role, so
    only a live Postgres proves the ``guild_isolation`` policy actually admits
    both halves: the INSERT must satisfy WITH CHECK for the tenant it seeds, and
    the DELETE must be scoped to that tenant — a ``guild_admin`` row in a guild
    the user is not acting on (and that Discord did not report) must survive.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.guilds import service as guilds_service

    owner_engine = create_async_engine(OWNER_URL)
    app_engine = create_async_engine(APP_URL)
    try:
        async with owner_engine.begin() as owner:
            g_a = await _seed_guild(owner, 960001)  # user holds guild_admin here
            g_b = await _seed_guild(owner, 960002)  # ...and none here
            g_c = await _seed_guild(owner, 960003)  # ...and one here, off Discord's list
            user_id = g_a.admin_user_id
            await owner.execute(
                text("INSERT INTO user_guild_roles (user_id, guild_id, role) VALUES (:u, :g, :r)"),
                {"u": user_id, "g": g_c.guild_id, "r": GUILD_ADMIN},
            )

        session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with session_factory() as session, session.begin():
            result = await guilds_service.sync_discord_admin_roles(
                session,
                user_id=user_id,
                # Discord says: admin of B only, and C is not in their guild list.
                admin_discord_guild_ids=[960002],
                member_discord_guild_ids=[960001, 960002],
            )
        assert result.granted == (str(g_b.guild_id),)
        assert result.revoked == (str(g_a.guild_id),)

        # Read back over the OWNER connection (bypasses RLS) — the ground truth.
        async with owner_engine.begin() as owner:
            rows = (
                (
                    await owner.execute(
                        text(
                            "SELECT guild_id FROM user_guild_roles "
                            "WHERE user_id = :u AND role = :r ORDER BY guild_id"
                        ),
                        {"u": str(user_id), "r": GUILD_ADMIN},
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(str(g) for g in rows) == sorted([str(g_b.guild_id), str(g_c.guild_id)]), (
            "B granted, A revoked, and C — absent from Discord's list — left alone"
        )
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()
