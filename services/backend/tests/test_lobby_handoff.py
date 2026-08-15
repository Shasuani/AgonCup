"""Locking in must lead somewhere: roster → lobby → the message that says where.

The gap these tests close was end-to-end and completely silent. Every piece of the
autonomous path existed and was tested in isolation — the succession seeding, the
lobby spec, the credentials DM — but nothing ever wrote a ``ClientSession``, which
is the *only* table :func:`initialize_lobby` builds its roster from. So a bracket
was drawn, ten people locked in, the roster came back empty, ``NoEligibleHostError``
was swallowed as a best-effort no-op, and not one player was told anything.

Three joins are asserted here, because breaking any one of them restores that
silence:

* lock-in writes the player's standing client record (:func:`record_client_presence`),
* drawing the bracket binds those records to a match (:func:`bind_match_roster`),
* a match with no opted-in host still hands the players a lobby by hand.

See PLAN.md §3.6.1 / §3.3.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import (
    GameMode,
    LobbyPhase,
    MatchStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TournamentStatus,
)

from app.models import (
    ClientSession,
    Match,
    MatchLobby,
    RiotAccount,
    Team,
    TeamMember,
    Tournament,
    User,
)
from app.orchestration import service as orchestration
from app.tournaments import service
from tests._fake_session import FakeSession

_CODE = "ABCD2345"


async def _capture_noop(_publisher: object, **_kwargs: object) -> None:
    """Swallow the public match_ready embed — this test asserts the DMs only."""
    return None


class _NullPublisher:
    """Swallows orchestration commands — these tests assert DB state, not wire bytes."""

    async def publish(self, envelope: object) -> None:  # noqa: D102 - test double
        return None


def _tournament(*, guild_id: uuid.UUID, game_mode: GameMode) -> Tournament:
    return Tournament(
        id=uuid.uuid4(),
        guild_id=guild_id,
        name="Cup",
        game_mode=game_mode,
        status=TournamentStatus.OPEN,
        max_teams=16,
        join_code=_CODE,
    )


def _verified(session: FakeSession, user_id: uuid.UUID, tag: str) -> RiotAccount:
    """A player who finished account verification — i.e. one an invite can address."""
    account = RiotAccount(
        id=uuid.uuid4(),
        user_id=user_id,
        puuid=f"puuid-{tag}",
        summoner_id=f"sid-{tag}",
        verified=True,
    )
    session.store[RiotAccount].append(account)
    return account


async def _lock_in(session: FakeSession, tournament: Tournament, user_id: uuid.UUID) -> None:
    await service.join_by_code(session, code=_CODE, user_id=user_id)
    await service.check_in_self(session, tournament_id=tournament.id, user_id=user_id)


# ---------------------------------------------------------------------------
# record_client_presence — the write that was missing entirely
# ---------------------------------------------------------------------------
async def test_locking_in_records_the_player_as_a_client() -> None:
    guild_id, alice = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    _verified(session, alice, "alice")

    await _lock_in(session, tournament, alice)

    rows = session.store[ClientSession]
    assert len(rows) == 1
    assert rows[0].user_id == alice
    assert rows[0].puuid == "puuid-alice"
    # Standing, not bound: it is a preference record until a bracket exists.
    assert rows[0].match_id is None


async def test_a_player_without_a_riot_account_locks_in_but_is_not_a_client() -> None:
    """No PUUID means no invite target — so there is nothing to write, and lock-in
    must still succeed rather than fail on somebody's unfinished verification."""
    guild_id, alice = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)

    await _lock_in(session, tournament, alice)

    assert session.store[ClientSession] == []


async def test_hosting_is_opt_in_and_a_later_lock_in_does_not_revoke_it() -> None:
    """Hosting drives LCU *writes*, so it is never on by default (PLAN.md §3.6.1)
    — and once given, a plain re-lock-in carries no answer and must not clear it."""
    guild_id, alice = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    _verified(session, alice, "alice")

    await _lock_in(session, tournament, alice)
    assert session.store[ClientSession][0].host_opt_in is False

    await service.check_in_self(
        session, tournament_id=tournament.id, user_id=alice, host_opt_in=True
    )
    assert session.store[ClientSession][0].host_opt_in is True

    await service.check_in_self(session, tournament_id=tournament.id, user_id=alice)
    assert session.store[ClientSession][0].host_opt_in is True
    # Still exactly one standing row — re-locking in never duplicates a player.
    assert len(session.store[ClientSession]) == 1


# ---------------------------------------------------------------------------
# bind_match_roster — the join between a drawn bracket and a buildable lobby
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_binding_copies_standing_records_onto_the_match() -> None:
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.SUMMONERS_RIFT_5V5)
    session.store[Tournament].append(tournament)

    teams = []
    for user_id, tag in ((alice, "alice"), (bob, "bob")):
        team = Team(id=uuid.uuid4(), guild_id=guild_id, name=f"Team {tag}", captain_user_id=user_id)
        session.store[Team].append(team)
        session.store[TeamMember].append(
            TeamMember(
                guild_id=guild_id,
                team_id=team.id,
                user_id=user_id,
                role=TeamMemberRole.CAPTAIN,
                status=TeamMemberStatus.ACTIVE,
            )
        )
        _verified(session, user_id, tag)
        await service.record_client_presence(session, user_id=user_id, host_opt_in=True)
        teams.append(team)

    match = Match(
        id=uuid.uuid4(),
        guild_id=guild_id,
        tournament_id=tournament.id,
        round=0,
        bracket_slot=0,
        team_a=teams[0].id,
        team_b=teams[1].id,
        status=MatchStatus.PENDING,
    )
    session.store[Match].append(match)

    assert await orchestration.bind_match_roster(session, match=match) == 2

    bound = [c for c in session.store[ClientSession] if c.match_id == match.id]
    assert {c.puuid for c in bound} == {"puuid-alice", "puuid-bob"}
    # The consent answer travels with the copy — the lobby is built from these rows.
    assert all(c.host_opt_in for c in bound)
    # And the standing rows are copied, not consumed.
    assert len([c for c in session.store[ClientSession] if c.match_id is None]) == 2

    # Idempotent: re-running a bracket start must not double the roster.
    assert await orchestration.bind_match_roster(session, match=match) == 0
    assert len([c for c in session.store[ClientSession] if c.match_id == match.id]) == 2


# ---------------------------------------------------------------------------
# The whole chain: two players lock in, and somebody is told where to play
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_started_1v1_hands_the_players_a_lobby() -> None:
    """The exact scenario that produced nothing: a 1v1, both players locked in.

    Neither opted in to hosting (no client does yet), so this lands on the manual
    fallback — which is the point. The assertion is that the match ends up with
    credentials somebody can act on, instead of silence.
    """
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    _verified(session, alice, "alice")
    _verified(session, bob, "bob")

    for player in (alice, bob):
        await _lock_in(session, tournament, player)

    await service.start_tournament(session, tournament_id=tournament.id)
    opened = await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )

    assert opened == 1
    match = session.store[Match][0]
    # Both players are on the match roster, which is what the lobby is built from.
    assert len([c for c in session.store[ClientSession] if c.match_id == match.id]) == 2

    lobby = session.store[MatchLobby][0]
    assert lobby.match_id == match.id
    assert lobby.state is LobbyPhase.HOST_LOST  # nobody opted in — manual fallback
    assert lobby.lobby_name and lobby.lobby_password


@pytest.mark.asyncio
async def test_both_1v1_players_are_actually_dmed_the_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Asserts the recipients, not just that a lobby row appeared.

    The DM goes to the match's *captains*, and in 1v1 each entrant is the captain
    of their own solo team — so both players must be reached. Getting this wrong
    would leave a lobby recorded and still nobody told, which is the exact failure
    this whole path exists to prevent.
    """
    import app.notifications as notifications

    sent: list[str] = []

    async def _capture(_publisher: object, **kwargs: object) -> None:
        sent.append(str(kwargs["discord_user_id"]))

    monkeypatch.setattr(notifications, "publish_lobby_credentials", _capture)
    monkeypatch.setattr(notifications, "publish_match_ready", _capture_noop)

    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    for user_id, tag, snowflake in ((alice, "alice", "111"), (bob, "bob", "222")):
        session.store[User].append(User(id=user_id, display_name=tag, discord_user_id=snowflake))
        _verified(session, user_id, tag)

    for player in (alice, bob):
        await _lock_in(session, tournament, player)
    await service.start_tournament(session, tournament_id=tournament.id)
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )

    assert sorted(sent) == ["111", "222"]


@pytest.mark.asyncio
async def test_a_player_can_find_the_match_their_client_must_subscribe_to() -> None:
    """The lookup that lets a client join its own match's realtime channel.

    Until this existed the only way a desktop client subscribed to a match was a
    match id pasted into ``DESKTOP_MATCH_ID``, so in a real tournament nobody was
    on the channel and every command the backend published went to an empty room.
    It doubles as what the Live match screen renders.
    """
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    for user_id, tag in ((alice, "alice"), (bob, "bob")):
        session.store[User].append(User(id=user_id, display_name=tag))
        _verified(session, user_id, tag)

    await service.join_by_code(session, code=_CODE, user_id=alice)
    await service.check_in_self(
        session, tournament_id=tournament.id, user_id=alice, host_opt_in=True
    )
    await _lock_in(session, tournament, bob)
    await service.start_tournament(session, tournament_id=tournament.id)
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )

    mine = await orchestration.list_my_matches(session, user_id=bob)
    assert len(mine) == 1
    entry = mine[0]
    assert entry.match_id == str(session.store[Match][0].id)
    # The guild is carried so the client can scope its subscribe frame correctly.
    assert entry.guild_id == str(guild_id)
    assert entry.tournament_name == "Cup"
    # Bob is not the host, and is told who is — never a raw PUUID.
    assert entry.is_host is False
    assert entry.host_name == "alice"
    assert entry.lobby_state == LobbyPhase.AWAITING_CREATE.value

    # ...and the host sees the same match flagged as theirs to create.
    assert (await orchestration.list_my_matches(session, user_id=alice))[0].is_host is True


@pytest.mark.asyncio
async def test_a_completed_match_drops_off_the_client_list() -> None:
    """Nothing left to do, so nothing to subscribe to."""
    guild_id, alice = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    _verified(session, alice, "alice")
    await service.record_client_presence(session, user_id=alice)

    match = Match(
        id=uuid.uuid4(),
        guild_id=guild_id,
        tournament_id=tournament.id,
        round=0,
        bracket_slot=0,
        status=MatchStatus.COMPLETED,
    )
    session.store[Match].append(match)
    standing = session.store[ClientSession][0]
    session.store[ClientSession].append(
        ClientSession(guild_id=guild_id, user_id=alice, puuid=standing.puuid, match_id=match.id)
    )

    assert await orchestration.list_my_matches(session, user_id=alice) == []


@pytest.mark.asyncio
async def test_an_opted_in_player_gets_the_automated_lobby_instead() -> None:
    """Same tournament, one difference: somebody let their client host.

    Proves the manual fallback is a fallback and not the only outcome — the same
    start now leases a host and drives the lobby through the state machine.
    """
    guild_id = uuid.uuid4()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session = FakeSession()
    session.current_guild = str(guild_id)
    tournament = _tournament(guild_id=guild_id, game_mode=GameMode.ARAM_1V1)
    session.store[Tournament].append(tournament)
    _verified(session, alice, "alice")
    _verified(session, bob, "bob")

    await service.join_by_code(session, code=_CODE, user_id=alice)
    await service.check_in_self(
        session, tournament_id=tournament.id, user_id=alice, host_opt_in=True
    )
    await _lock_in(session, tournament, bob)

    await service.start_tournament(session, tournament_id=tournament.id)
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )

    lobby = session.store[MatchLobby][0]
    assert lobby.host_puuid == "puuid-alice"  # the only opted-in candidate
    assert lobby.state is LobbyPhase.AWAITING_CREATE


def test_the_first_round_is_called_round_one() -> None:
    """The bracket engine numbers rounds from 1, so the label must not add one.

    Every player-facing label built ``Round {match.round + 1}``, which announced
    the opening round of every tournament as "Round 2" — in the Discord embed and
    the client's match list. ``bracket_slot`` genuinely is 0-based and still takes
    the +1, which is how the two got conflated.
    """
    from app.tournaments.readiness import match_label

    first = Match(
        id=uuid.uuid4(),
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=1,
        bracket_slot=0,
        status=MatchStatus.PENDING,
    )
    assert match_label(first) == "Round 1 · Match 1"

    second = Match(
        id=uuid.uuid4(),
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=2,
        bracket_slot=3,
        status=MatchStatus.PENDING,
    )
    assert match_label(second) == "Round 2 · Match 4"
