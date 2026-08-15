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
    GameMode,
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
    # Defaults to the 5v5 Rift when no mode is given.
    assert t.game_mode == GameMode.SUMMONERS_RIFT_5V5
    # Delegated insert derived guild_id from the GUC set by the bridge.
    assert session.current_guild == str(guild.id)
    assert str(t.guild_id) == str(guild.id)
    # The Manage-Guild assertion authorized the action but must NOT have seeded a
    # durable guild_admin role row (no permanent backdoor from a transient flag).
    assert session.store[UserGuildRole] == []


@pytest.mark.asyncio
async def test_create_tournament_1v1_aram_mode() -> None:
    session = FakeSession()
    await _seed_onboarded_guild(session)

    t = await bridge.create_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
        name="Duel Night",
        game_mode=GameMode.ARAM_1V1,
        best_of=3,
    )
    assert t.game_mode == GameMode.ARAM_1V1
    assert t.best_of == 3


@pytest.mark.asyncio
async def test_create_tournament_honours_the_requested_format() -> None:
    """The bot can pick the bracket shape, like the web console always could.

    The bridge used to take no ``format`` at all, so every tournament created from
    Discord was single-elimination whatever the admin asked for.
    """
    from lol_schemas.enums import TournamentFormat

    session = FakeSession()
    await _seed_onboarded_guild(session)

    t = await bridge.create_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
        name="Double Cup",
        format=TournamentFormat.DOUBLE_ELIMINATION,
    )
    assert t.format == TournamentFormat.DOUBLE_ELIMINATION


@pytest.mark.asyncio
async def test_create_tournament_rejects_invalid_best_of() -> None:
    from app.tournaments import service as tournaments_service

    session = FakeSession()
    await _seed_onboarded_guild(session)
    with pytest.raises(tournaments_service.InvalidStateError):
        await bridge.create_tournament(
            session,
            ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
            name="Bad Series",
            best_of=4,
        )


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


# ---------------------------------------------------------------------------
# Self-service account list / unlink (delegated to app.accounts.service)
# ---------------------------------------------------------------------------
def _seed_riot_account(
    session: FakeSession, user_id: uuid.UUID, *, game_name: str, tag_line: str
) -> None:
    from app.models import RiotAccount

    acct = RiotAccount(
        user_id=user_id,
        puuid=f"{game_name}-{tag_line}-puuid",
        riot_id_game_name=game_name,
        riot_id_tag_line=tag_line,
        region="eun1",
        verified=True,
    )
    acct.id = uuid.uuid4()
    session.store[RiotAccount].append(acct)


@pytest.mark.asyncio
async def test_list_my_accounts_returns_only_owned() -> None:
    from app.models import RiotAccount  # noqa: F401 (store key)

    session = FakeSession()
    me = User(id=uuid.uuid4(), discord_user_id=DISCORD_MEMBER, display_name="Me")
    other = User(id=uuid.uuid4(), discord_user_id=DISCORD_MEMBER + 1, display_name="Other")
    session.store[User].extend([me, other])
    _seed_riot_account(session, me.id, game_name="Chama", tag_line="Shasu")
    _seed_riot_account(session, other.id, game_name="NotMine", tag_line="X")

    rows = await bridge.list_my_accounts(
        session, ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False)
    )
    assert {(r.game_name, r.tag_line) for r in rows} == {("Chama", "Shasu")}


@pytest.mark.asyncio
async def test_unlink_my_account_removes_owned() -> None:
    from app.models import RiotAccount

    session = FakeSession()
    me = User(id=uuid.uuid4(), discord_user_id=DISCORD_MEMBER, display_name="Me")
    session.store[User].append(me)
    _seed_riot_account(session, me.id, game_name="Chama", tag_line="Shasu")

    removed = await bridge.unlink_my_account(
        session,
        ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False),
        game_name="Chama",
        tag_line="Shasu",
    )
    assert removed.game_name == "Chama"
    assert session.store[RiotAccount] == []


@pytest.mark.asyncio
async def test_unlink_my_account_unknown_raises() -> None:
    from app.accounts import service as accounts_service

    session = FakeSession()
    me = User(id=uuid.uuid4(), discord_user_id=DISCORD_MEMBER, display_name="Me")
    session.store[User].append(me)
    _seed_riot_account(session, me.id, game_name="Chama", tag_line="Shasu")

    with pytest.raises(accounts_service.AccountNotLinked):
        await bridge.unlink_my_account(
            session,
            ctx=_ctx(discord_user_id=DISCORD_MEMBER, is_admin=False),
            game_name="Ghost",
            tag_line="X",
        )


# ---------------------------------------------------------------------------
# /tournament-start: resolve by the code the admin was given, and open the round
# ---------------------------------------------------------------------------
async def _seed_locked_in_duel(session: FakeSession, guild) -> Tournament:  # type: ignore[no-untyped-def]
    """A 1v1 with two players locked in — one press away from being playable."""
    from app.models import RiotAccount
    from app.tournaments import service as tournaments_service

    # The bridge sets this itself; the seeding below runs before it and needs a
    # tenant of its own (every guild-scoped write fails closed without one).
    session.current_guild = str(guild.id)
    tournament = Tournament(
        id=uuid.uuid4(),
        guild_id=guild.id,
        name="Duel Night",
        game_mode=GameMode.ARAM_1V1,
        status=TournamentStatus.OPEN,
        max_teams=16,
        join_code="ABCD2345",
    )
    session.store[Tournament].append(tournament)
    for tag in ("alice", "bob"):
        player = User(id=uuid.uuid4(), display_name=tag)
        session.store[User].append(player)
        acct = RiotAccount(
            id=uuid.uuid4(),
            user_id=player.id,
            puuid=f"puuid-{tag}",
            summoner_id=f"sid-{tag}",
            verified=True,
        )
        session.store[RiotAccount].append(acct)
        await tournaments_service.join_by_code(session, code="ABCD2345", user_id=player.id)
        await tournaments_service.check_in_self(
            session, tournament_id=tournament.id, user_id=player.id
        )
    return tournament


@pytest.mark.asyncio
async def test_starting_from_discord_by_join_code_hands_the_match_to_its_players(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The two halves of ``/tournament-start`` that were missing.

    The admin has ONE identifier for the tournament they just made — the join code
    the create form printed — and it was the one thing the command could not
    resolve: the code's alphabet is hex-compatible, so it was read as a short id
    prefix and matched nothing.

    And starting from Discord only drew the bracket. The SPA's start has always
    gone on to open the round; this one returned "3 matches, 2 rounds" and left
    everybody exactly where they were, which is indistinguishable from nothing
    having happened.
    """
    from app.models import Match, MatchLobby
    from app.orchestration import service as orchestration_service

    monkeypatch.setattr(orchestration_service, "make_command_publisher", lambda: _NullPublisher())

    session = FakeSession()
    guild = await _seed_onboarded_guild(session)
    tournament = await _seed_locked_in_duel(session, guild)

    found, result = await bridge.start_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
        tournament_ref="abcd-2345",  # hand-typed: lower case, hyphenated
    )

    assert found.id == tournament.id
    assert result.matches_created == 1
    assert tournament.status == TournamentStatus.RUNNING
    assert len(session.store[Match]) == 1
    # Handed over, not just drawn: a lobby with credentials and a named opener.
    lobby = session.store[MatchLobby][0]
    assert lobby.lobby_name and lobby.lobby_password
    assert lobby.host_puuid in {"puuid-alice", "puuid-bob"}


@pytest.mark.asyncio
async def test_a_name_still_wins_over_a_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Adding the code to the chain must not shadow the older way of naming one."""
    from app.orchestration import service as orchestration_service

    monkeypatch.setattr(orchestration_service, "make_command_publisher", lambda: _NullPublisher())

    session = FakeSession()
    guild = await _seed_onboarded_guild(session)
    await _seed_locked_in_duel(session, guild)

    found, _ = await bridge.start_tournament(
        session,
        ctx=_ctx(discord_user_id=DISCORD_ADMIN, is_admin=True),
        tournament_ref="Duel Night",
    )
    assert found.join_code == "ABCD2345"


class _NullPublisher:
    """Swallows commands: this file is offline and asserts DB state, not wire bytes."""

    async def publish(self, envelope: object) -> None:  # noqa: D102 - test double
        return None
