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

import uuid

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


# ---------------------------------------------------------------------------
# sync_discord_admin_roles — Discord's ADMINISTRATOR bit mirrored to role rows
# ---------------------------------------------------------------------------
OTHER_DISCORD_GUILD = 222333444555666


def _add_guild(
    session: FakeSession, discord_guild_id: int, *, status: GuildStatus = GuildStatus.ACTIVE
) -> Guild:
    guild = Guild(discord_guild_id=discord_guild_id, name="G", status=status, settings={})
    guild.id = uuid.uuid4()
    session.store[Guild].append(guild)
    return guild


def _roles(session: FakeSession, guild: Guild) -> list[UserGuildRoleName]:
    return [r.role for r in session.store[UserGuildRole] if str(r.guild_id) == str(guild.id)]


@pytest.mark.asyncio
async def test_sync_grants_guild_admin_to_a_discord_administrator() -> None:
    """The gap this closes: a Discord admin who never invited the bot had no row,
    so every tenant request 403'd on ``get_tenant_session``."""
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    guild = _add_guild(session, DISCORD_GUILD)

    result = await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        admin_discord_guild_ids=[DISCORD_GUILD],
        member_discord_guild_ids=[DISCORD_GUILD],
    )

    assert _roles(session, guild) == [UserGuildRoleName.GUILD_ADMIN]
    assert result.granted == (str(guild.id),)
    assert result.revoked == ()


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_does_not_duplicate_the_row() -> None:
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    guild = _add_guild(session, DISCORD_GUILD)
    ids = {"admin_discord_guild_ids": [DISCORD_GUILD], "member_discord_guild_ids": [DISCORD_GUILD]}

    await guilds.sync_discord_admin_roles(session, user_id=user.id, **ids)
    second = await guilds.sync_discord_admin_roles(session, user_id=user.id, **ids)

    assert _roles(session, guild) == [UserGuildRoleName.GUILD_ADMIN]
    assert second.granted == ()


@pytest.mark.asyncio
async def test_sync_revokes_when_the_discord_admin_bit_is_gone() -> None:
    """The half that makes this a sync, not a grant: losing ADMINISTRATOR on
    Discord loses ``guild_admin`` here at the next login."""
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    guild = _add_guild(session, DISCORD_GUILD)
    session.store[UserGuildRole].append(
        UserGuildRole(user_id=user.id, guild_id=guild.id, role=UserGuildRoleName.GUILD_ADMIN)
    )

    result = await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        admin_discord_guild_ids=[],
        member_discord_guild_ids=[DISCORD_GUILD],
    )

    assert _roles(session, guild) == []
    assert result.revoked == (str(guild.id),)


@pytest.mark.asyncio
async def test_sync_only_touches_guild_admin_rows() -> None:
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    guild = _add_guild(session, DISCORD_GUILD)
    session.store[UserGuildRole].extend(
        [
            UserGuildRole(user_id=user.id, guild_id=guild.id, role=UserGuildRoleName.GUILD_ADMIN),
            UserGuildRole(user_id=user.id, guild_id=guild.id, role=UserGuildRoleName.CAPTAIN),
            UserGuildRole(
                user_id=user.id, guild_id=guild.id, role=UserGuildRoleName.PLATFORM_OPERATOR
            ),
        ]
    )

    await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        admin_discord_guild_ids=[],
        member_discord_guild_ids=[DISCORD_GUILD],
    )

    # Revoking admin must not demote a captain or strip cross-tenant staff.
    assert sorted(r.value for r in _roles(session, guild)) == ["captain", "platform_operator"]


@pytest.mark.asyncio
async def test_sync_leaves_guilds_the_user_is_not_in_alone() -> None:
    """A guild absent from the Discord list (they left, or the page truncated) is
    NOT evidence of a revocation, so its role row must survive."""
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    present = _add_guild(session, DISCORD_GUILD)
    absent = _add_guild(session, OTHER_DISCORD_GUILD)
    session.store[UserGuildRole].append(
        UserGuildRole(user_id=user.id, guild_id=absent.id, role=UserGuildRoleName.GUILD_ADMIN)
    )

    await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        admin_discord_guild_ids=[DISCORD_GUILD],
        member_discord_guild_ids=[DISCORD_GUILD],
    )

    assert _roles(session, present) == [UserGuildRoleName.GUILD_ADMIN]
    assert _roles(session, absent) == [UserGuildRoleName.GUILD_ADMIN]


@pytest.mark.asyncio
async def test_sync_ignores_discord_guilds_that_never_onboarded() -> None:
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    removed = _add_guild(session, DISCORD_GUILD, status=GuildStatus.REMOVED)

    result = await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        # Admin of a removed guild AND of one we have never heard of.
        admin_discord_guild_ids=[DISCORD_GUILD, OTHER_DISCORD_GUILD],
        member_discord_guild_ids=[DISCORD_GUILD, OTHER_DISCORD_GUILD],
    )

    assert result.granted == ()
    assert _roles(session, removed) == []


@pytest.mark.asyncio
async def test_sync_clears_the_tenant_guc_when_done() -> None:
    """Login reads roles ACROSS guilds next; a lingering GUC would scope that read."""
    session = FakeSession()
    user = await guilds.upsert_user_by_discord_id(session, discord_user_id=DISCORD_ADMIN)
    _add_guild(session, DISCORD_GUILD)

    await guilds.sync_discord_admin_roles(
        session,
        user_id=user.id,
        admin_discord_guild_ids=[DISCORD_GUILD],
        member_discord_guild_ids=[DISCORD_GUILD],
    )

    assert not session.current_guild
