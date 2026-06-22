"""Discord bot bridge tests (PLAN.md §3.2 multi-tenant bot, service-auth).

Offline by design — no network, no real DB, no Discord gateway (a dict-backed fake
session mirroring ``tests/test_auth.py``). Three contract guarantees are covered:

* **Service-token gate** (:func:`require_service_token`): 503 when the backend has
  no ``bot_service_token`` configured (fail closed), 401 on a missing/mismatched
  token, pass on the correct constant-time match.
* **Guild-admin gating** through the bridge: a Manage-Guild admin can create a
  tournament; a plain member is rejected (NotGuildAdminError → 403).
* **Delegated tournament-join happy path**: the bridge resolves user+guild, sets
  the RLS GUC, and DELEGATES to ``app.tournaments.service.register_team`` (no
  duplicated registration logic) producing a PENDING registration.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from lol_schemas.enums import (
    GuildStatus,
    RegistrationStatus,
    TournamentStatus,
)

from app.guilds import service as guilds
from app.integrations.discord import service as bridge
from app.integrations.discord.service import DiscordContext
from app.integrations.discord.service_auth import require_service_token
from app.models import Guild, Registration, Team, Tournament, User, UserGuildRole
from tests._fake_session import FakeSession

DISCORD_GUILD = 222333444555666
DISCORD_ADMIN = 1234567890123456
DISCORD_MEMBER = 6543210987654321


class _Settings:
    """Minimal stand-in for app.config.Settings (only the token field is read)."""

    def __init__(self, token: str) -> None:
        self.bot_service_token = token


# ---------------------------------------------------------------------------
# Service-token gate (401 / 503)
# ---------------------------------------------------------------------------
def test_service_token_unconfigured_is_503() -> None:
    # Fail closed: no token configured → the bridge is unusable, not open.
    with pytest.raises(HTTPException) as exc:
        require_service_token(settings=_Settings(""), x_service_token="anything")
    assert exc.value.status_code == 503


def test_service_token_missing_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_service_token(settings=_Settings("s3cret"), x_service_token=None)
    assert exc.value.status_code == 401


def test_service_token_mismatch_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_service_token(settings=_Settings("s3cret"), x_service_token="wrong")
    assert exc.value.status_code == 401


def test_service_token_match_passes() -> None:
    # Correct token → returns None (the gate's only job is the side-effect check).
    assert require_service_token(settings=_Settings("s3cret"), x_service_token="s3cret") is None


# ---------------------------------------------------------------------------
# Fixtures: an onboarded guild + acting users
# ---------------------------------------------------------------------------
async def _seed_onboarded_guild(session: FakeSession) -> Guild:
    guild = Guild(
        discord_guild_id=DISCORD_GUILD, name="Arena", status=GuildStatus.ACTIVE, settings={}
    )
    guild.id = uuid.uuid4()
    session.store[Guild].append(guild)
    return guild


def _ctx(*, discord_user_id: int, is_admin: bool) -> DiscordContext:
    return DiscordContext(
        discord_user_id=discord_user_id,
        discord_guild_id=DISCORD_GUILD,
        is_discord_guild_admin=is_admin,
    )


# ---------------------------------------------------------------------------
# Guild-admin gating via the bridge (admin vs non-admin)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_tournament_admin_allowed() -> None:
    session = FakeSession()
    guild = await _seed_onboarded_guild(session)

    t = await bridge.create_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
        name="Summer Cup",
    )
    assert isinstance(t, Tournament)
    assert t.status == TournamentStatus.DRAFT
    # Delegated insert derived guild_id from the GUC set by the bridge.
    assert session.current_guild == str(guild.id)
    assert str(t.guild_id) == str(guild.id)
    # The Manage-Guild assertion authorized the action but must NOT have seeded a
    # durable guild_admin role row (no permanent backdoor from a transient flag).
    assert session.store[UserGuildRole] == []


@pytest.mark.asyncio
async def test_create_tournament_non_admin_rejected() -> None:
    session = FakeSession()
    await _seed_onboarded_guild(session)

    with pytest.raises(guilds.NotGuildAdminError):
        await bridge.create_tournament(
            session,
            ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False),
            name="Sneaky Cup",
        )
    # Nothing was created and no admin role leaked to the plain member.
    assert session.store[Tournament] == []
    assert session.store[UserGuildRole] == []


@pytest.mark.asyncio
async def test_bridge_call_on_unonboarded_guild_raises() -> None:
    session = FakeSession()  # no guild seeded
    with pytest.raises(guilds.GuildNotOnboardedError):
        await bridge.list_tournaments(
            session, ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False)
        )


# ---------------------------------------------------------------------------
# Delegated tournament-join happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_join_tournament_happy_path_delegates() -> None:
    session = FakeSession()
    guild = await _seed_onboarded_guild(session)

    # Seed an OPEN tournament + a team in the same guild (the team the user joins).
    captain = User(discord_user_id=999, display_name="cap")
    captain.id = uuid.uuid4()
    session.store[User].append(captain)

    tournament = Tournament(
        guild_id=guild.id, name="Open Cup", status=TournamentStatus.OPEN, max_teams=16
    )
    tournament.id = uuid.uuid4()
    session.store[Tournament].append(tournament)

    team = Team(guild_id=guild.id, name="Wolves", captain_user_id=captain.id)
    team.id = uuid.uuid4()
    session.store[Team].append(team)

    reg = await bridge.join_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False),
        tournament_id=tournament.id,
        team_id=team.id,
    )

    # Delegated to app.tournaments.service.register_team → PENDING registration.
    assert isinstance(reg, Registration)
    assert reg.status == RegistrationStatus.PENDING
    assert str(reg.tournament_id) == str(tournament.id)
    assert str(reg.team_id) == str(team.id)
    assert str(reg.guild_id) == str(guild.id)
    assert session.store[Registration] == [reg]
    # The acting member exists (upserted) and the GUC scoped the delegated insert.
    assert any(u.discord_user_id == DISCORD_MEMBER for u in session.store[User])
    assert session.current_guild == str(guild.id)


@pytest.mark.asyncio
async def test_join_tournament_full_propagates_delegated_error() -> None:
    session = FakeSession()
    guild = await _seed_onboarded_guild(session)

    tournament = Tournament(
        guild_id=guild.id, name="Tiny Cup", status=TournamentStatus.OPEN, max_teams=2
    )
    tournament.id = uuid.uuid4()
    session.store[Tournament].append(tournament)

    # Two teams already registered → at the cap.
    for name in ("A", "B"):
        cap = User(discord_user_id=hash(name) & 0xFFFFFFFF, display_name=name)
        cap.id = uuid.uuid4()
        session.store[User].append(cap)
        team = Team(guild_id=guild.id, name=name, captain_user_id=cap.id)
        team.id = uuid.uuid4()
        session.store[Team].append(team)
        session.store[Registration].append(
            Registration(
                guild_id=guild.id,
                tournament_id=tournament.id,
                team_id=team.id,
                status=RegistrationStatus.PENDING,
            )
        )

    late = Team(guild_id=guild.id, name="Late", captain_user_id=session.store[User][0].id)
    late.id = uuid.uuid4()
    session.store[Team].append(late)

    # The delegated cap check (app.tournaments.service) raises — bridge does not
    # re-implement it.
    from app.tournaments import service as tournaments_service

    with pytest.raises(tournaments_service.TournamentFullError):
        await bridge.join_tournament(
            session,
            ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False),
            tournament_id=tournament.id,
            team_id=late.id,
        )


# ---------------------------------------------------------------------------
# Account linking — desktop code mint (delegated to app.accounts.service)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_registration_mints_code_with_origin_guild() -> None:
    from app.accounts import service as accounts_service
    from app.models import VerificationAttempt

    session = FakeSession()
    guild = await _seed_onboarded_guild(session)

    result = await bridge.start_registration(
        session, ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False)
    )

    attempt = session.store[VerificationAttempt][0]
    assert attempt.verify_kind == accounts_service.VERIFY_KIND_DESKTOP_CODE
    assert attempt.status == accounts_service.STATUS_PENDING
    assert str(attempt.origin_guild_id) == str(guild.id)
    assert result.origin_guild_id == str(guild.id)
    assert result.code  # grouped display form
    # The acting user was upserted (resolved by Discord snowflake).
    assert any(u.discord_user_id == DISCORD_MEMBER for u in session.store[User])


@pytest.mark.asyncio
async def test_start_registration_tolerates_unonboarded_guild() -> None:
    from app.models import VerificationAttempt

    session = FakeSession()  # no guild seeded
    result = await bridge.start_registration(
        session, ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False)
    )
    # Linking is global — a non-onboarded guild simply leaves origin_guild_id NULL.
    assert result.origin_guild_id is None
    assert session.store[VerificationAttempt][0].origin_guild_id is None
