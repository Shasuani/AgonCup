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

import json
import uuid
from typing import Any, cast

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


class _RecordingPublisher:
    """Keeps every published envelope, so a test can read what the clients were told."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def publish(self, envelope: Any) -> None:  # noqa: D102 - test double
        self.sent.append(envelope)


def _command_body(envelope: Any) -> dict[str, Any]:
    """The command as the desktop receives it (aliased/camelCase wire form)."""
    return cast(dict[str, Any], json.loads(envelope.payload.model_dump_json()))


def _command_type(envelope: Any) -> str:
    return str(_command_body(envelope)["type"])


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
async def test_exactly_one_player_is_dmed_the_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Asserts the recipient, not just that a lobby row appeared.

    ONE person is told to make the lobby. The credentials used to go to every
    captain — in 1v1 that is both players — which reads like an instruction to
    each of them, and two people who both follow it produce two custom games with
    one player each. The one told is the designated creator, and it is the same
    player the other client is told to wait for.
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

    lobby = session.store[MatchLobby][0]
    creator = next(
        u for u in session.store[User] if u.id == _user_for_puuid(session, lobby.host_puuid)
    )
    assert sent == [str(creator.discord_user_id)]


def _user_for_puuid(session: FakeSession, puuid: str | None) -> uuid.UUID:
    return next(a.user_id for a in session.store[RiotAccount] if a.puuid == puuid)


@pytest.mark.asyncio
async def test_the_named_creator_is_told_to_open_it_and_the_others_to_wait() -> None:
    """The two halves of naming a creator, seen from each client.

    A match nobody's client will host still has a designated opener, and both
    screens are driven off the same ``host_puuid``: theirs says "this one is
    yours", everybody else's names them so nine people are not each waiting to be
    the one who acts.
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

    for player in (alice, bob):
        await _lock_in(session, tournament, player)
    await service.start_tournament(session, tournament_id=tournament.id)
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )

    lobby = session.store[MatchLobby][0]
    assert lobby.state is LobbyPhase.HOST_LOST  # still nobody's client hosting...
    assert lobby.host_puuid is not None  # ...but somebody is named to open it.

    creator_id = _user_for_puuid(session, lobby.host_puuid)
    other_id = bob if creator_id == alice else alice
    creator_view = (await orchestration.list_my_matches(session, user_id=creator_id))[0]
    other_view = (await orchestration.list_my_matches(session, user_id=other_id))[0]

    assert creator_view.is_host is True
    assert other_view.is_host is False
    assert other_view.host_name == next(
        u.display_name for u in session.store[User] if u.id == creator_id
    )
    # Both still see the credentials — the DM is a nudge, not the only copy.
    assert creator_view.lobby_password == other_view.lobby_password
    assert creator_view.lobby_password


@pytest.mark.asyncio
async def test_a_hand_made_lobby_still_invites_and_auto_accepts() -> None:
    """The manual path is manual for ONE step: the create.

    It used to be manual for all of them — credentials in a DM and nine people
    expected to find a custom game by name. The two commands that close that gap
    are the invite (to the creator, who is the only one who can send it) and the
    auto-accept fenced to the creator's PUUID (to everyone, so nobody has to click).
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

    for player in (alice, bob):
        await _lock_in(session, tournament, player)
    await service.start_tournament(session, tournament_id=tournament.id)

    publisher = _RecordingPublisher()
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=publisher
    )

    lobby = session.store[MatchLobby][0]
    kinds = [_command_type(e) for e in publisher.sent]
    # No create: the human makes the lobby, so commanding a client to make one too
    # is how you end up with two.
    assert "create_lobby_command" not in kinds
    assert kinds.count("invite_players_command") == 1
    assert kinds.count("auto_accept_command") == 1

    invite = next(e for e in publisher.sent if _command_type(e) == "invite_players_command")
    accept = next(e for e in publisher.sent if _command_type(e) == "auto_accept_command")
    # The creator invites everyone but themselves...
    assert _command_body(invite)["toPuuids"] == [
        p for p in ("puuid-alice", "puuid-bob") if p != lobby.host_puuid
    ]
    # ...and the guests only accept from the creator, never "anyone in the lobby".
    assert _command_body(accept)["expectedInviterPuuid"] == lobby.host_puuid


def test_the_creator_is_a_captain_of_the_home_side_and_the_same_one_every_time() -> None:
    """Whoever is named must still be named after a restart.

    The choice is re-derived, never stored anywhere but the lobby row, so an
    unstable rule would hand a re-scan to a different player and put a second
    custom game on the board. Captain first (a team already looks to them), team A
    before team B, PUUID last — all three arbitrary, all three *stable*.
    """
    match = Match(
        id=uuid.uuid4(),
        guild_id=uuid.uuid4(),
        tournament_id=uuid.uuid4(),
        round=1,
        bracket_slot=0,
        team_a=uuid.uuid4(),
        team_b=uuid.uuid4(),
        status=MatchStatus.PENDING,
    )
    roster = [
        orchestration.InviteCandidate(
            puuid="p-a-grunt", user_id=uuid.uuid4(), team_id=match.team_a, is_captain=False
        ),
        orchestration.InviteCandidate(
            puuid="p-b-captain", user_id=uuid.uuid4(), team_id=match.team_b, is_captain=True
        ),
        orchestration.InviteCandidate(
            puuid="p-a-captain", user_id=uuid.uuid4(), team_id=match.team_a, is_captain=True
        ),
    ]

    picked = orchestration.pick_lobby_creator(roster, match=match)
    assert picked is not None and picked.puuid == "p-a-captain"
    # Order of the roster query must not change who is named.
    assert orchestration.pick_lobby_creator(list(reversed(roster)), match=match) == picked
    # A match with no clients attached names nobody rather than guessing.
    assert orchestration.pick_lobby_creator([], match=match) is None


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


@pytest.mark.asyncio
async def test_a_client_that_subscribes_late_is_told_again_what_to_do() -> None:
    """Commands are published once, to whoever happens to be listening.

    Redis pub/sub keeps no history, and the gap is structural: the commands go out
    when the bracket starts, while a client only learns the match exists on its
    next poll — later still for somebody who opens the app after the round began.
    Every one of them subscribed to a channel whose instructions had already been
    spoken. Subscribing therefore replays them.
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
        await _lock_in(session, tournament, user_id)
    await service.start_tournament(session, tournament_id=tournament.id)
    await orchestration.open_playable_matches(
        session, tournament_id=tournament.id, publisher=_NullPublisher()
    )
    match = session.store[Match][0]

    publisher = _RecordingPublisher()
    replayed = await orchestration.republish_lobby_commands(
        session, match_id=match.id, publisher=publisher
    )

    kinds = [_command_type(e) for e in publisher.sent]
    assert replayed == len(publisher.sent) == 2
    # Still no create: the lobby is the human's to make, and a replayed create is
    # how a match that already has one ends up with two.
    assert sorted(kinds) == ["auto_accept_command", "invite_players_command"]


@pytest.mark.asyncio
async def test_the_replay_stops_once_the_match_is_over() -> None:
    """Nothing to drive — replaying would ask a client to re-open a finished match."""
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
        round=1,
        bracket_slot=0,
        status=MatchStatus.COMPLETED,
    )
    session.store[Match].append(match)
    session.store[MatchLobby].append(
        MatchLobby(
            id=uuid.uuid4(),
            guild_id=guild_id,
            match_id=match.id,
            host_puuid="puuid-alice",
            host_succession=[],
            host_epoch=0,
            lobby_name="LoL-x",
            lobby_password="X",
            state=LobbyPhase.HOST_LOST,
        )
    )

    publisher = _RecordingPublisher()
    assert (
        await orchestration.republish_lobby_commands(
            session, match_id=match.id, publisher=publisher
        )
        == 0
    )
    assert publisher.sent == []


@pytest.mark.asyncio
async def test_the_replay_does_not_interrupt_a_lobby_that_is_already_playing() -> None:
    """A reconnect mid-game must not be answered with "make the lobby".

    The replay exists for clients that missed the start; past the point where the
    custom game is formed there is nothing to catch up on, and a create issued
    into champion select is an interruption rather than a repair.
    """
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
        round=1,
        bracket_slot=0,
        status=MatchStatus.PENDING,
    )
    session.store[Match].append(match)
    lobby = MatchLobby(
        id=uuid.uuid4(),
        guild_id=guild_id,
        match_id=match.id,
        host_puuid="puuid-alice",
        host_succession=["puuid-alice"],
        host_epoch=0,
        lobby_name="LoL-x",
        lobby_password="X",
        state=LobbyPhase.AWAITING_CREATE,
    )
    session.store[MatchLobby].append(lobby)

    # Still forming: the client is told to create it.
    first = _RecordingPublisher()
    await orchestration.republish_lobby_commands(session, match_id=match.id, publisher=first)
    assert "create_lobby_command" in [_command_type(e) for e in first.sent]

    # In champion select: nothing to replay at all.
    lobby.state = LobbyPhase.STARTING
    later = _RecordingPublisher()
    assert (
        await orchestration.republish_lobby_commands(session, match_id=match.id, publisher=later)
        == 0
    )
    assert later.sent == []
