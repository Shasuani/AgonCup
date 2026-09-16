"""Region-locked tournaments — one Riot platform per tournament (PLAN.md §3.1).

A verified Riot account lives on exactly one platform (``euw1``/``eun1``/...),
and an LCU lobby invite is addressed by a per-server ``summonerId`` — an EUW
client physically cannot invite an EUNE player. A tournament therefore carries
the platform it is played on, chosen at creation:

* ``create_tournament`` validates the region (KR is rejected — out of scope).
* ``join_by_code`` bounces entrants without a verified account on the platform —
  the WHOLE roster on the captain's team join, so the error names the blockers.
* ``check_in_self`` re-checks at lock-in (accounts can be unlinked after joining).
* ``record_client_presence`` prefers the account ON the platform when a player
  holds several (one per server), so the lobby invites the right identity.

NULL region = no restriction (legacy tournaments keep working). Offline tests on
the shared FakeSession; the DB column itself ships in migration 0005.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import GameMode, TeamMemberRole, TeamMemberStatus, TournamentStatus

from app.models import ClientSession, RiotAccount, Team, TeamMember, Tournament, User
from app.tournaments import service
from tests._fake_session import FakeSession

_CODE = "ABCD2345"  # already-normalized join code (alphabet-valid)


def _tournament(
    *, guild_id: uuid.UUID, game_mode: GameMode = GameMode.ARAM_1V1, region: str | None
) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Cup",
        game_mode=game_mode,
        status=TournamentStatus.OPEN,
        max_teams=16,
        join_code=_CODE,
        region=region,
    )


def _account(
    session: FakeSession,
    user_id: uuid.UUID,
    *,
    region: str | None,
    verified: bool = True,
    tag: str = "x",
    game_name: str | None = None,
    tag_line: str | None = None,
    lcu_puuid: str | None = None,
) -> RiotAccount:
    account = RiotAccount(
        id=uuid.uuid4(),
        user_id=user_id,
        puuid=f"puuid-{tag}",
        summoner_id=f"sid-{tag}",
        riot_id_game_name=game_name,
        riot_id_tag_line=tag_line,
        lcu_puuid=lcu_puuid,
        region=region,
        verified=verified,
    )
    session.store[RiotAccount].append(account)
    return account


def _user(session: FakeSession, name: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name=name))
    return user_id


def _member(
    session: FakeSession, *, guild_id: uuid.UUID, team_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    session.store[TeamMember].append(
        TeamMember(
            team_id=team_id,
            user_id=user_id,
            guild_id=guild_id,
            role=TeamMemberRole.PLAYER,
            status=TeamMemberStatus.ACTIVE,
        )
    )


# ---------------------------------------------------------------------------
# parse_tournament_region — creation-time validation
# ---------------------------------------------------------------------------
def test_region_none_passes_through() -> None:
    assert service.parse_tournament_region(None) is None


def test_region_is_case_insensitive_and_canonicalized() -> None:
    assert service.parse_tournament_region("EUW1") == "euw1"
    assert service.parse_tournament_region("eun1") == "eun1"


def test_unknown_region_is_rejected() -> None:
    with pytest.raises(service.InvalidStateError):
        service.parse_tournament_region("atlantis")


def test_kr_is_rejected() -> None:
    """Korea is out of scope (docs/compliance-checklist.md) — never creatable."""
    with pytest.raises(service.InvalidStateError):
        service.parse_tournament_region("kr")


async def test_create_tournament_stores_the_canonical_region() -> None:
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)

    t = await service.create_tournament(session, name="EUNE Cup", region="EUN1")

    assert t.region == "eun1"


async def test_create_tournament_without_region_is_unrestricted() -> None:
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)

    t = await service.create_tournament(session, name="Anywhere Cup")

    assert t.region is None


# ---------------------------------------------------------------------------
# join_by_code — the gate (solo path)
# ---------------------------------------------------------------------------
async def test_solo_join_is_blocked_without_an_account_on_the_platform() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region="eun1"))
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name="Giorgos"))

    with pytest.raises(service.RegionAccountRequiredError) as exc:
        await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert exc.value.region_label == "EUNE"
    assert exc.value.missing_names == ["Giorgos"]
    assert "EUNE" in str(exc.value)


async def test_solo_join_is_blocked_when_the_only_account_is_the_wrong_region() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region="eun1"))
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name="Giorgos"))
    _account(session, user_id, region="euw1", verified=True)

    with pytest.raises(service.RegionAccountRequiredError):
        await service.join_by_code(session, code=_CODE, user_id=user_id)


async def test_solo_join_is_blocked_when_the_matching_account_is_unverified() -> None:
    """Possession of the platform is what matters, and only verify proves it."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region="eun1"))
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name="Giorgos"))
    _account(session, user_id, region="eun1", verified=False)

    with pytest.raises(service.RegionAccountRequiredError):
        await service.join_by_code(session, code=_CODE, user_id=user_id)


async def test_solo_join_passes_with_a_verified_account_on_the_platform() -> None:
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region="eun1"))
    _account(session, user_id, region="eun1", verified=True)

    _, enrolment = await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert enrolment.user_id == user_id


async def test_a_second_account_on_the_platform_also_passes() -> None:
    """A user may hold one account per server; ANY verified match admits them."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region="eun1"))
    _account(session, user_id, region="euw1", verified=True, tag="euw")
    _account(session, user_id, region="eun1", verified=True, tag="eune")

    _, enrolment = await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert enrolment.user_id == user_id


async def test_a_regionless_tournament_restricts_nobody() -> None:
    """NULL region = the pre-region behaviour, so legacy tournaments still work."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(_tournament(guild_id=guild_id, region=None))

    _, enrolment = await service.join_by_code(session, code=_CODE, user_id=user_id)

    assert enrolment.user_id == user_id


# ---------------------------------------------------------------------------
# join_by_code — the gate (team path: the WHOLE roster, named on failure)
# ---------------------------------------------------------------------------
async def test_team_join_names_the_teammates_who_block_it() -> None:
    """The captain must learn WHICH teammates cannot play here, not just that
    somebody can't — otherwise the error is undebuggable from Discord/desktop."""
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5, region="eun1")
    )
    captain = _user(session, "Cap")
    mate = _user(session, "Bob")
    team = Team(id=uuid.uuid4(), guild_id=guild_id, name="Alpha", captain_user_id=captain)
    session.store[Team].append(team)
    _member(session, guild_id=guild_id, team_id=team.id, user_id=captain)
    _member(session, guild_id=guild_id, team_id=team.id, user_id=mate)
    _account(session, captain, region="eun1", verified=True, tag="cap")  # captain is fine

    with pytest.raises(service.RegionAccountRequiredError) as exc:
        await service.join_by_code(session, code=_CODE, user_id=captain)

    assert exc.value.missing_names == ["Bob"]


async def test_team_join_passes_when_the_whole_roster_is_on_the_platform() -> None:
    guild_id = uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    session.store[Tournament].append(
        _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5, region="eun1")
    )
    captain = _user(session, "Cap")
    mate = _user(session, "Bob")
    team = Team(id=uuid.uuid4(), guild_id=guild_id, name="Alpha", captain_user_id=captain)
    session.store[Team].append(team)
    _member(session, guild_id=guild_id, team_id=team.id, user_id=captain)
    _member(session, guild_id=guild_id, team_id=team.id, user_id=mate)
    _account(session, captain, region="eun1", verified=True, tag="cap")
    _account(session, mate, region="eun1", verified=True, tag="bob")

    _, reg = await service.join_by_code(session, code=_CODE, user_id=captain)

    assert reg.team_id == team.id


# ---------------------------------------------------------------------------
# check_in_self — the gate holds at lock-in too
# ---------------------------------------------------------------------------
async def test_lock_in_is_blocked_when_the_account_went_away_after_joining() -> None:
    """Joining proved the account; lock-in must re-check (unlink happens)."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, region="eun1")
    session.store[Tournament].append(tournament)
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name="Giorgos"))
    account = _account(session, user_id, region="eun1", verified=True)

    await service.join_by_code(session, code=_CODE, user_id=user_id)
    account.verified = False  # unlinked/re-verified elsewhere since joining

    with pytest.raises(service.RegionAccountRequiredError):
        await service.check_in_self(session, tournament_id=tournament.id, user_id=user_id)


# ---------------------------------------------------------------------------
# record_client_presence — WHICH account the lobby will invite
# ---------------------------------------------------------------------------
async def test_presence_prefers_the_account_on_the_tournaments_platform() -> None:
    """Two verified accounts, one per server: the platform match must win —
    the PUUID written here is who the lobby invites, and invites are per-server."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _account(session, user_id, region="euw1", verified=True, tag="euw")
    eune = _account(session, user_id, region="eun1", verified=True, tag="eune")

    row = await service.record_client_presence(session, user_id=user_id, region="eun1")

    assert row is not None
    assert row.puuid == eune.puuid
    assert session.store[ClientSession][0].puuid == eune.puuid


async def test_presence_without_a_region_still_prefers_the_verified_account() -> None:
    """The old pick order survives for regionless tournaments: verified first."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _account(session, user_id, region="euw1", verified=False, tag="euw")
    verified = _account(session, user_id, region="eun1", verified=True, tag="eune")

    row = await service.record_client_presence(session, user_id=user_id)

    assert row is not None
    assert row.puuid == verified.puuid


# ---------------------------------------------------------------------------
# record_client_presence — the account OPEN in the client wins (ToDoList #8)
# ---------------------------------------------------------------------------
def _two_eune_accounts(session: FakeSession, user_id: uuid.UUID) -> tuple[RiotAccount, RiotAccount]:
    """Two verified accounts on the SAME server; the id tie-break alone would
    pick whichever sorts first — i.e. possibly the one that is signed out."""
    main = _account(
        session,
        user_id,
        region="eun1",
        tag="main",
        game_name="Shasuani",
        tag_line="EUNE",
        lcu_puuid="lcu-main",
    )
    smurf = _account(
        session,
        user_id,
        region="eun1",
        tag="smurf",
        game_name="Smurfy",
        tag_line="1234",
        lcu_puuid="lcu-smurf",
    )
    # Force the tie-break to prefer the OTHER account, so a passing test proves
    # the active-account hint did the work rather than luck of the uuid draw.
    main.id, smurf.id = sorted((main.id, smurf.id))[1], sorted((main.id, smurf.id))[0]
    return main, smurf


async def test_presence_invites_the_account_signed_into_the_client() -> None:
    """Two accounts on the tournament's server: the one the desktop saw open in
    League is the one recorded, matched by Riot ID (case-insensitive)."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    main, smurf = _two_eune_accounts(session, user_id)

    row = await service.record_client_presence(
        session,
        user_id=user_id,
        region="eun1",
        active_account=service.ActiveAccount(game_name="shasuani", tag_line="eune"),
    )

    assert row is not None
    assert row.puuid == main.puuid
    assert row.puuid != smurf.puuid


async def test_presence_matches_the_open_account_by_lcu_ids_when_the_riot_id_is_missing() -> None:
    """A renamed account still matches on the LCU puuid / summoner id hints."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    main, smurf = _two_eune_accounts(session, user_id)

    by_lcu = await service.record_client_presence(
        session,
        user_id=user_id,
        region="eun1",
        active_account=service.ActiveAccount(lcu_puuid="lcu-main"),
    )
    assert by_lcu is not None and by_lcu.puuid == main.puuid

    by_sid = await service.record_client_presence(
        session,
        user_id=user_id,
        region="eun1",
        active_account=service.ActiveAccount(summoner_id=smurf.summoner_id),
    )
    assert by_sid is not None and by_sid.puuid == smurf.puuid


async def test_presence_never_lets_the_open_account_override_the_platform() -> None:
    """Signed into EUW while the tournament is on EUNE: an invite cannot cross
    servers, so the EUNE account is still the one recorded."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    euw = _account(session, user_id, region="euw1", tag="euw", game_name="Me", tag_line="EUW")
    eune = _account(session, user_id, region="eun1", tag="eune", game_name="Me", tag_line="EUNE")

    row = await service.record_client_presence(
        session,
        user_id=user_id,
        region="eun1",
        active_account=service.ActiveAccount(game_name="Me", tag_line="EUW", region="euw1"),
    )

    assert row is not None
    assert row.puuid == eune.puuid
    assert row.puuid != euw.puuid


async def test_presence_falls_back_to_the_old_pick_when_the_open_account_is_not_linked() -> None:
    """An unknown Riot ID re-orders nothing: verified-first, then the id tie-break."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    _account(session, user_id, region="eun1", verified=False, tag="pending")
    verified = _account(session, user_id, region="eun1", verified=True, tag="ok")

    row = await service.record_client_presence(
        session,
        user_id=user_id,
        region="eun1",
        active_account=service.ActiveAccount(game_name="Nobody", tag_line="0000"),
    )

    assert row is not None
    assert row.puuid == verified.puuid


async def test_lock_in_records_the_account_open_in_the_client() -> None:
    """End to end through the lock-in button: the standing ClientSession — the
    row the lobby roster is built from — carries the OPEN account's PUUID."""
    guild_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, region="eun1")
    session.store[Tournament].append(tournament)
    session.store[User].append(User(id=user_id, discord_user_id=1, display_name="Giorgos"))
    main, smurf = _two_eune_accounts(session, user_id)
    await service.join_by_code(session, code=_CODE, user_id=user_id)

    await service.check_in_self(
        session,
        tournament_id=tournament.id,
        user_id=user_id,
        active_account=service.ActiveAccount(
            game_name="Smurfy", tag_line="1234", lcu_puuid="lcu-smurf", summoner_id="sid-smurf"
        ),
    )

    standing = [c for c in session.store[ClientSession] if c.match_id is None]
    assert [c.puuid for c in standing] == [smurf.puuid]
    assert standing[0].puuid != main.puuid
