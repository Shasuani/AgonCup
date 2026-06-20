"""Guild onboarding/upsert + per-guild settings tests (PLAN.md §3.2).

Offline by design — no network, no real DB (a dict-backed fake session, mirroring
``tests/test_auth.py``). Covers:

* :class:`GuildSettings` parse/serialize/merge (PATCH semantics).
* :func:`upsert_user_by_discord_id` create + idempotent reuse + display refresh.
* :func:`onboard_guild` first-join create vs re-sync, settings application, and
  the seeded ``guild_admin`` role row (idempotent on re-sync).
* :func:`resolve_guild_by_discord_id` 404/removed → GuildNotOnboardedError.
* :func:`ensure_guild_admin` gating: Manage-Guild admin seeds + passes, a persisted
  role passes, a plain member is rejected.
"""

from __future__ import annotations

import pytest
from lol_schemas.enums import GuildStatus, UserGuildRoleName

from app.guilds import service as guilds
from app.guilds.settings import GuildSettings, GuildSettingsPatch
from app.models import Guild, User, UserGuildRole
from tests._fake_session import FakeSession

DISCORD_GUILD = 111222333444555
DISCORD_ADMIN = 9988776655443322
DISCORD_MEMBER = 1010101010101010


# ---------------------------------------------------------------------------
# GuildSettings model
# ---------------------------------------------------------------------------
def test_settings_defaults_and_roundtrip() -> None:
    s = GuildSettings.from_jsonb(None)
    assert s.admin_role_ids == []
    assert s.notify_channel_id is None
    assert s.central_feed_enabled is False
    assert s.to_jsonb() == {
        "admin_role_ids": [],
        "notify_channel_id": None,
        "central_feed_enabled": False,
    }


def test_settings_from_jsonb_ignores_extra_keys() -> None:
    s = GuildSettings.from_jsonb({"notify_channel_id": 42, "bogus": "x"})
    assert s.notify_channel_id == 42


def test_settings_merge_is_patch_semantics() -> None:
    base = GuildSettings(admin_role_ids=[1, 2], notify_channel_id=7, central_feed_enabled=False)
    merged = base.merge(GuildSettingsPatch(central_feed_enabled=True))
    # Only the set field changes; the rest are preserved.
    assert merged.central_feed_enabled is True
    assert merged.admin_role_ids == [1, 2]
    assert merged.notify_channel_id == 7


# ---------------------------------------------------------------------------
# User upsert by discord snowflake
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upsert_user_creates_then_reuses() -> None:
    session = FakeSession()
    u1 = await guilds.upsert_user_by_discord_id(
        session, discord_user_id=DISCORD_MEMBER, display_name="alice"
    )
    assert u1.discord_user_id == DISCORD_MEMBER
    assert len(session.store[User]) == 1

    u2 = await guilds.upsert_user_by_discord_id(
        session, discord_user_id=DISCORD_MEMBER, display_name="alice-renamed"
    )
    # Same row reused; display name refreshed (non-identity display data).
    assert u2 is u1
    assert len(session.store[User]) == 1
    assert u2.display_name == "alice-renamed"


# ---------------------------------------------------------------------------
# Onboarding (GUILD_JOIN upsert + admin seed)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_onboard_creates_guild_and_seeds_admin() -> None:
    session = FakeSession()
    result = await guilds.onboard_guild(
        session,
        discord_guild_id=DISCORD_GUILD,
        name="Test Guild",
        admin_discord_user_id=DISCORD_ADMIN,
        admin_display_name="boss",
        settings=GuildSettingsPatch(notify_channel_id=555, central_feed_enabled=True),
    )
    assert result.created is True
    assert result.admin_seeded is True
    assert result.settings.notify_channel_id == 555
    assert result.settings.central_feed_enabled is True

    guild = session.store[Guild][0]
    assert guild.discord_guild_id == DISCORD_GUILD
    assert guild.status == GuildStatus.ACTIVE
    # The GUC was set to the resolved guild for the seed insert (RLS scoping).
    assert session.current_guild == str(guild.id)

    roles = session.store[UserGuildRole]
    assert len(roles) == 1
    assert roles[0].role == UserGuildRoleName.GUILD_ADMIN
    assert str(roles[0].guild_id) == str(guild.id)


@pytest.mark.asyncio
async def test_onboard_is_idempotent_resync() -> None:
    session = FakeSession()
    first = await guilds.onboard_guild(
        session,
        discord_guild_id=DISCORD_GUILD,
        name="Old Name",
        admin_discord_user_id=DISCORD_ADMIN,
    )
    second = await guilds.onboard_guild(
        session,
        discord_guild_id=DISCORD_GUILD,
        name="New Name",
        admin_discord_user_id=DISCORD_ADMIN,
        settings=GuildSettingsPatch(notify_channel_id=999),
    )
    # Re-sync: same guild row, refreshed name/settings, admin already seeded.
    assert second.created is False
    assert second.admin_seeded is False
    assert second.guild_id == first.guild_id
    assert len(session.store[Guild]) == 1
    assert session.store[Guild][0].name == "New Name"
    assert second.settings.notify_channel_id == 999
    # Exactly one guild_admin role row (idempotent seed).
    assert len(session.store[UserGuildRole]) == 1


@pytest.mark.asyncio
async def test_onboard_reactivates_removed_guild() -> None:
    session = FakeSession()
    removed = Guild(
        discord_guild_id=DISCORD_GUILD,
        name="Gone",
        status=GuildStatus.REMOVED,
        settings={},
    )
    removed.id = __import__("uuid").uuid4()
    removed.removed_at = __import__("datetime").datetime.now()
    session.store[Guild].append(removed)

    result = await guilds.onboard_guild(
        session,
        discord_guild_id=DISCORD_GUILD,
        name="Back",
        admin_discord_user_id=DISCORD_ADMIN,
    )
    assert result.created is False
    assert removed.status == GuildStatus.ACTIVE
    assert removed.removed_at is None


# ---------------------------------------------------------------------------
# Guild resolution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolve_unknown_guild_raises() -> None:
    session = FakeSession()
    with pytest.raises(guilds.GuildNotOnboardedError):
        await guilds.resolve_guild_by_discord_id(session, discord_guild_id=DISCORD_GUILD)


@pytest.mark.asyncio
async def test_resolve_removed_guild_raises() -> None:
    session = FakeSession()
    g = Guild(discord_guild_id=DISCORD_GUILD, name="X", status=GuildStatus.REMOVED, settings={})
    g.id = __import__("uuid").uuid4()
    session.store[Guild].append(g)
    with pytest.raises(guilds.GuildNotOnboardedError):
        await guilds.resolve_guild_by_discord_id(session, discord_guild_id=DISCORD_GUILD)


# ---------------------------------------------------------------------------
# Guild-admin gating
# ---------------------------------------------------------------------------
async def _make_guild_and_user(session: FakeSession) -> tuple[Guild, User]:
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_MEMBER)
    guild = Guild(discord_guild_id=DISCORD_GUILD, name="G", status=GuildStatus.ACTIVE, settings={})
    guild.id = __import__("uuid").uuid4()
    session.store[Guild].append(guild)
    await guilds.set_tenant_guild(session, guild_id=guild.id)
    return guild, user


@pytest.mark.asyncio
async def test_ensure_admin_authorizes_manage_guild_without_seeding() -> None:
    session = FakeSession()
    guild, user = await _make_guild_and_user(session)
    # Manage-Guild admin authorizes THIS request but must NOT seed a durable
    # guild_admin row (security: no permanent backdoor from a transient assertion).
    await guilds.ensure_guild_admin(session, user=user, guild=guild, is_discord_guild_admin=True)
    assert session.store[UserGuildRole] == []


@pytest.mark.asyncio
async def test_ensure_admin_accepts_persisted_role_without_manage_guild() -> None:
    session = FakeSession()
    guild, user = await _make_guild_and_user(session)
    session.store[UserGuildRole].append(
        UserGuildRole(user_id=user.id, guild_id=guild.id, role=UserGuildRoleName.GUILD_ADMIN)
    )
    # Not Manage-Guild this session, but holds the persisted role → passes, no dup.
    await guilds.ensure_guild_admin(session, user=user, guild=guild, is_discord_guild_admin=False)
    assert len(session.store[UserGuildRole]) == 1


@pytest.mark.asyncio
async def test_ensure_admin_rejects_plain_member() -> None:
    session = FakeSession()
    guild, user = await _make_guild_and_user(session)
    with pytest.raises(guilds.NotGuildAdminError):
        await guilds.ensure_guild_admin(
            session, user=user, guild=guild, is_discord_guild_admin=False
        )
