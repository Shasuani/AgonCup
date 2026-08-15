"""Orchestration service tests (PLAN.md §3.6.1, §7).

Offline by design — no network, no real database. A small in-memory fake session
(mirroring ``tests/test_auth.py``'s ``FakeSession``) implements the narrow
``AsyncSession`` surface the orchestration code touches: ``add``/``flush``,
``select(MatchLobby|Match|ClientSession)``, the ``current_setting('app.current_guild')``
GUC read, a ``refresh`` no-op, and — critically — a **conditional UPDATE** with a
real ``rowcount`` so the epoch-fencing (``WHERE host_epoch = :expected``) is
exercised end-to-end.

The pure decision logic lives in ``lol_core`` and is exhaustively tested there;
here we test the *service wiring*: epoch-fenced host re-designation (the
conditional update), stale-epoch ack/heartbeat rejection, and the
promote-vs-recreate decision path (which command envelope is published).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from lol_core import FailoverAction, LobbyEvent
from lol_realtime import AutoAcceptCommand, CreateLobbyCommand, PromoteHostCommand
from lol_schemas.enums import LobbyPhase

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
from app.orchestration import service
from app.orchestration.service import (
    RecordingPublisher,
    auto_initialize_lobby,
    default_lobby_credentials,
    derive_server_seed,
    ingest_heartbeat,
    ingest_lobby_state,
    initialize_lobby,
    redesignate_host,
    roster_from_sessions,
    seed_host_succession,
    trigger_failover,
)

_GUILD = uuid.uuid4()
_SECRET = "test-server-secret"


# ---------------------------------------------------------------------------
# In-memory fake AsyncSession (only what the orchestration code uses)
# ---------------------------------------------------------------------------
@dataclass
class _Result:
    rows: list[Any]
    rowcount: int = 0

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self.rows

    def __iter__(self) -> Any:
        # Real SQLAlchemy ScalarResult is iterable (list(session.execute(...).scalars())).
        return iter(self.rows)


class FakeSession:
    """A dict-backed stand-in for AsyncSession covering the orchestration paths."""

    def __init__(self, *, guild_id: uuid.UUID | None = _GUILD) -> None:
        self.store: dict[type, list[Any]] = {
            Match: [],
            MatchLobby: [],
            ClientSession: [],
            # Invite ordering joins the roster to its teams and the summonerId
            # an LCU invite actually takes.
            RiotAccount: [],
            Team: [],
            TeamMember: [],
            Tournament: [],
            User: [],
        }
        self.guild_id = guild_id

    # --- unit-of-work -----------------------------------------------------
    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def refresh(self, obj: Any) -> None:
        # The fake mutates rows in place, so the object is already fresh.
        return None

    # --- query dispatch ---------------------------------------------------
    async def execute(self, statement: Any, params: Any = None) -> Any:
        name = statement.__class__.__name__
        if name == "TextClause":
            # current_setting('app.current_guild') GUC read.
            return _Result([str(self.guild_id)] if self.guild_id else [None])
        if name == "Update":
            return self._execute_update(statement)
        return self._execute_select(statement)

    def _execute_select(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        model = _model_of(entity)
        crit = _equality_filters(statement)
        # A model with no seeded store (e.g. Tournament for the game-mode lookup)
        # yields no rows -> the caller falls back to its default (5v5).
        rows = [r for r in self.store.get(model, []) if _matches(r, crit)]
        return _Result(rows)

    def _execute_update(self, statement: Any) -> _Result:
        model = _model_of(statement.table)
        crit = _equality_filters(statement)
        values = dict(statement._values) if statement._values else {}
        affected = 0
        for r in self.store[model]:
            if _matches(r, crit):
                for col, val in values.items():
                    setattr(r, _col_name(col), _literal(val))
                affected += 1
        return _Result([], rowcount=affected)

    # --- transaction / context -------------------------------------------
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _model_of(entity: Any) -> type:
    if hasattr(entity, "class_"):
        return entity.class_
    if hasattr(entity, "entity"):
        return entity.entity
    name = getattr(entity, "name", None)
    for model in (
        Match,
        MatchLobby,
        ClientSession,
        RiotAccount,
        Team,
        TeamMember,
        Tournament,
        User,
    ):
        if model.__tablename__ == name:
            return model
    return entity


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    clauses = list(getattr(statement, "_where_criteria", ()) or ())
    for clause in clauses:
        for bool_expr in _flatten_and(clause):
            left = getattr(bool_expr, "left", None)
            right = getattr(bool_expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = _literal(right)
    return crit


def _flatten_and(clause: Any) -> list[Any]:
    if clause is None:
        return []
    if clause.__class__.__name__ == "BooleanClauseList":
        out: list[Any] = []
        for c in clause.clauses:
            out.extend(_flatten_and(c))
        return out
    return [clause]


def _literal(value: Any) -> Any:
    return getattr(value, "value", value)


def _col_name(col: Any) -> str:
    return getattr(col, "key", str(col))


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    for key, val in crit.items():
        actual = getattr(row, key, None)
        # `col.in_([...])` binds a sequence rather than a scalar — the invite
        # ordering resolves teams and summonerIds that way, and treating the
        # list as a scalar silently matched nothing.
        if isinstance(val, (list, tuple, set, frozenset)):
            if str(actual) not in {str(v) for v in val}:
                return False
        elif str(actual) != str(val):
            return False
    return True


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------
def _match(match_id: uuid.UUID) -> Match:
    m = Match(
        guild_id=_GUILD,
        tournament_id=uuid.uuid4(),
        round=1,
        bracket_slot=0,
    )
    m.id = match_id
    return m


def _client_session(match_id: uuid.UUID, puuid: str, *, host_opt_in: bool) -> ClientSession:
    cs = ClientSession(
        guild_id=_GUILD,
        user_id=uuid.uuid4(),
        puuid=puuid,
        match_id=match_id,
        host_opt_in=host_opt_in,
    )
    cs.id = uuid.uuid4()
    return cs


def _lobby(
    match_id: uuid.UUID,
    *,
    host_puuid: str,
    succession: list[str],
    host_epoch: int = 0,
    state: LobbyPhase = LobbyPhase.AWAITING_CREATE,
) -> MatchLobby:
    lobby = MatchLobby(
        guild_id=_GUILD,
        match_id=match_id,
        host_puuid=host_puuid,
        host_succession=succession,
        host_epoch=host_epoch,
        lobby_name="match-lobby",
        lobby_password="pw",
        state=state,
    )
    lobby.id = uuid.uuid4()
    return lobby


def _seeded_session(*, opt_ins: int, opt_outs: int = 0) -> tuple[FakeSession, uuid.UUID, list[str]]:
    """A session with a match + roster; returns (session, match_id, succession)."""
    session = FakeSession()
    match_id = uuid.uuid4()
    session.store[Match].append(_match(match_id))
    puuids = [f"puuid-{i}" for i in range(opt_ins + opt_outs)]
    for i, puuid in enumerate(puuids):
        session.store[ClientSession].append(
            _client_session(match_id, puuid, host_opt_in=i < opt_ins)
        )
        # An LCU invite takes a numeric summonerId, so an unverified player is
        # simply not invitable — every roster here is verified.
        session.store[RiotAccount].append(
            RiotAccount(user_id=uuid.uuid4(), puuid=puuid, summoner_id=f"sid-{i}")
        )
    server_seed = derive_server_seed(match_id, server_secret=_SECRET)
    roster = roster_from_sessions(session.store[ClientSession])
    succession = list(seed_host_succession(roster, server_seed=server_seed))
    return session, match_id, succession


# ---------------------------------------------------------------------------
# derive_server_seed (pure)
# ---------------------------------------------------------------------------
def test_server_seed_is_deterministic_and_match_bound() -> None:
    mid = uuid.uuid4()
    other = uuid.uuid4()
    assert derive_server_seed(mid, server_secret=_SECRET) == derive_server_seed(
        mid, server_secret=_SECRET
    )
    # Different match id OR different secret => different seed (unsteerable).
    assert derive_server_seed(mid, server_secret=_SECRET) != derive_server_seed(
        other, server_secret=_SECRET
    )
    assert derive_server_seed(mid, server_secret=_SECRET) != derive_server_seed(
        mid, server_secret="other-secret"
    )


# ---------------------------------------------------------------------------
# initialize_lobby
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initialize_lobby_designates_host_and_publishes_create_invite() -> None:
    session, match_id, succession = _seeded_session(opt_ins=3)
    pub = RecordingPublisher()

    result = await initialize_lobby(
        session,
        match_id=match_id,
        lobby_name="lobby",
        lobby_password="pw",
        publisher=pub,
        server_secret=_SECRET,
    )

    # Host is the head of the deterministic succession, epoch 0, advanced past
    # ASSIGNED_HOST into AWAITING_CREATE via the pure state machine.
    assert result.host_puuid == succession[0]
    assert result.host_epoch == 0
    assert result.succession == tuple(succession)
    assert result.state == LobbyPhase.AWAITING_CREATE

    # CreateLobby + Invite + AutoAccept envelopes, all fenced at epoch 0. The
    # auto-accept is what saves the other nine from clicking Accept by hand — an
    # invite nobody accepts automatically is only half the automation.
    payload_types = [e.payload.type for e in pub.published]
    assert payload_types == [
        "create_lobby_command",
        "invite_players_command",
        "auto_accept_command",
    ]
    arm = pub.published[2].payload
    assert isinstance(arm, AutoAcceptCommand)
    # Fenced to THIS match's designated host — never "accept anything".
    assert arm.expected_inviter_puuid == succession[0]
    assert arm.host_epoch == 0
    create = pub.published[0].payload
    assert isinstance(create, CreateLobbyCommand)
    assert create.host_epoch == 0
    # Everyone on the roster EXCEPT the host, who is already in the lobby they
    # just created. This used to be an empty list: the spec carried no summoner
    # ids at all, so the host created a lobby and invited nobody.
    assert set(create.spec.invitee_puuids) == set(succession) - {result.host_puuid}
    # An LCU invite is keyed on summonerId, so the spec must carry them too —
    # a puuid-only payload is not something the client can act on.
    assert create.spec.invitee_summoner_ids
    assert len(create.spec.invitee_summoner_ids) == len(create.spec.invitee_puuids)


def test_default_lobby_credentials_are_deterministic() -> None:
    mid = uuid.uuid4()
    name, pw = default_lobby_credentials(mid)
    assert (name, pw) == default_lobby_credentials(mid)  # stable per match
    assert name.startswith("LoL-")
    assert len(pw) == 6
    # The password is uppercased hex, so it may legitimately be ALL DIGITS
    # ("088151"). `pw.isupper()` — what this asserted — is False for a string with
    # no cased characters at all, which flaked this test on roughly 6% of random
    # match ids ((10/16)**6). Assert the property that was actually meant: nothing
    # lowercase survives the .upper().
    assert pw == pw.upper()
    assert all(c in "0123456789ABCDEF" for c in pw)
    assert default_lobby_credentials(uuid.uuid4())[0] != name  # match-bound


def test_default_lobby_password_handles_an_all_digit_match_id() -> None:
    """The regression the flake was pointing at: a hex id with no letters.

    Pinned rather than random so it cannot silently stop covering this case.
    """
    mid = uuid.UUID("08815100-0000-4000-8000-000000000000")
    name, pw = default_lobby_credentials(mid)

    assert pw == "088151"
    assert name == "LoL-08815100"


@pytest.mark.asyncio
async def test_auto_initialize_lobby_spins_up_when_host_present() -> None:
    session, match_id, succession = _seeded_session(opt_ins=3)
    pub = RecordingPublisher()

    result = await auto_initialize_lobby(
        session, match_id=match_id, publisher=pub, server_secret=_SECRET
    )

    assert result is not None
    assert result.host_puuid == succession[0]
    assert result.state == LobbyPhase.AWAITING_CREATE
    assert [e.payload.type for e in pub.published] == [
        "create_lobby_command",
        "invite_players_command",
        "auto_accept_command",
    ]


@pytest.mark.asyncio
async def test_auto_initialize_lobby_falls_back_to_manual_without_host() -> None:
    """Nobody opted in to host → the players are handed the lobby, not left waiting.

    This used to be a silent no-op, which made "no eligible host" look exactly like
    "nobody started the match": no lobby, no message, ten people staring at a
    client. PLAN.md §3.6.1 falls back to the manual name+password instead.
    """
    session, match_id, _ = _seeded_session(opt_ins=0, opt_outs=2)
    pub = RecordingPublisher()

    result = await auto_initialize_lobby(
        session, match_id=match_id, publisher=pub, server_secret=_SECRET
    )

    # No host was leased, so no LCU command is issued to anyone.
    assert result is None
    assert pub.published == []

    # ...but the match is recorded as manual-fallback, carrying the same
    # deterministic credentials the automation would have used.
    name, password = default_lobby_credentials(match_id)
    lobby = session.store[MatchLobby][0]
    assert lobby.state is LobbyPhase.HOST_LOST
    assert lobby.host_puuid is None
    assert (lobby.lobby_name, lobby.lobby_password) == (name, password)


@pytest.mark.asyncio
async def test_manual_fallback_announces_once_however_often_it_is_retried() -> None:
    """The fallback DM must not repeat every time the round is re-scanned.

    ``open_playable_matches`` runs on every bracket start and every recorded
    result, so an announce with no memory would DM the same credentials again on
    each pass. The ``MatchLobby`` row written by the fallback is that memory.
    """
    session, match_id, _ = _seeded_session(opt_ins=0, opt_outs=2)

    for _ in range(3):
        await auto_initialize_lobby(
            session, match_id=match_id, publisher=RecordingPublisher(), server_secret=_SECRET
        )

    assert len(session.store[MatchLobby]) == 1


@pytest.mark.asyncio
async def test_initialize_lobby_is_idempotent() -> None:
    session, match_id, succession = _seeded_session(opt_ins=2)
    pub = RecordingPublisher()
    first = await initialize_lobby(
        session,
        match_id=match_id,
        lobby_name="l",
        lobby_password="p",
        publisher=pub,
        server_secret=_SECRET,
    )
    published_after_first = len(pub.published)
    second = await initialize_lobby(
        session,
        match_id=match_id,
        lobby_name="l",
        lobby_password="p",
        publisher=pub,
        server_secret=_SECRET,
    )
    assert second.lobby_id == first.lobby_id
    assert second.host_epoch == first.host_epoch
    # The replay published nothing new (no double create/invite).
    assert len(pub.published) == published_after_first


@pytest.mark.asyncio
async def test_initialize_lobby_no_host_optin_raises() -> None:
    session, match_id, _ = _seeded_session(opt_ins=0, opt_outs=3)
    with pytest.raises(service.NoEligibleHostError):
        await initialize_lobby(
            session,
            match_id=match_id,
            lobby_name="l",
            lobby_password="p",
            server_secret=_SECRET,
        )


# ---------------------------------------------------------------------------
# redesignate_host — conditional UPDATE WHERE host_epoch = expected (epoch fence)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_redesignate_host_wins_on_expected_epoch() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_puuid="a", succession=["a", "b"], host_epoch=0)
    session.store[MatchLobby].append(lobby)

    won = await redesignate_host(session, match_id=match_id, expected_epoch=0, new_host_puuid="b")
    assert won is True
    assert lobby.host_epoch == 1  # monotonic bump
    assert lobby.host_puuid == "b"


@pytest.mark.asyncio
async def test_redesignate_host_loses_on_stale_expected_epoch() -> None:
    # The lease already advanced to epoch 2; a worker still holding expected=0
    # matches zero rows and must back off (no double-increment, no overwrite).
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_puuid="winner", succession=["a", "b"], host_epoch=2)
    session.store[MatchLobby].append(lobby)

    won = await redesignate_host(
        session, match_id=match_id, expected_epoch=0, new_host_puuid="loser"
    )
    assert won is False
    assert lobby.host_epoch == 2  # untouched
    assert lobby.host_puuid == "winner"


# ---------------------------------------------------------------------------
# Stale-epoch rejection: heartbeat + lobby-state ack
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_heartbeat_rejects_stale_epoch() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_puuid="a", succession=["a"], host_epoch=2)
    session.store[MatchLobby].append(lobby)

    # Lower epoch (deposed/flapping host) is rejected fail-closed.
    with pytest.raises(service.StaleEpochError):
        await ingest_heartbeat(session, match_id=match_id, host_epoch=1, phase=LobbyPhase.FILLING)
    # Higher epoch (impossible against monotonic backend lease) is rejected too.
    with pytest.raises(service.StaleEpochError):
        await ingest_heartbeat(session, match_id=match_id, host_epoch=3, phase=LobbyPhase.FILLING)


@pytest.mark.asyncio
async def test_heartbeat_accepts_current_epoch_and_stamps_liveness() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_puuid="a", succession=["a"], host_epoch=2)
    session.store[MatchLobby].append(lobby)

    result = await ingest_heartbeat(
        session, match_id=match_id, host_epoch=2, phase=LobbyPhase.FILLING
    )
    assert result.accepted is True
    assert lobby.last_host_heartbeat_at is not None  # liveness stamped


@pytest.mark.asyncio
async def test_lobby_state_ack_rejects_stale_epoch_before_transition() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="a",
        succession=["a"],
        host_epoch=2,
        state=LobbyPhase.AWAITING_CREATE,
    )
    session.store[MatchLobby].append(lobby)

    with pytest.raises(service.StaleEpochError):
        await ingest_lobby_state(
            session, match_id=match_id, host_epoch=1, event=LobbyEvent.LOBBY_CREATED
        )
    # State must NOT have advanced on a rejected (stale) ack.
    assert lobby.state == LobbyPhase.AWAITING_CREATE


@pytest.mark.asyncio
async def test_lobby_state_ack_advances_via_pure_machine() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="a",
        succession=["a"],
        host_epoch=0,
        state=LobbyPhase.AWAITING_CREATE,
    )
    session.store[MatchLobby].append(lobby)

    result = await ingest_lobby_state(
        session,
        match_id=match_id,
        host_epoch=0,
        event=LobbyEvent.LOBBY_CREATED,
        lcu_lobby_id="lcu-123",
    )
    assert result.state == LobbyPhase.LOBBY_OPEN
    assert lobby.lcu_lobby_id == "lcu-123"


@pytest.mark.asyncio
async def test_lobby_state_ack_rejects_illegal_transition() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="a",
        succession=["a"],
        host_epoch=0,
        state=LobbyPhase.AWAITING_CREATE,
    )
    session.store[MatchLobby].append(lobby)

    # GAME_ENDED is not a legal edge out of AWAITING_CREATE.
    with pytest.raises(service.InvalidStateError):
        await ingest_lobby_state(
            session, match_id=match_id, host_epoch=0, event=LobbyEvent.GAME_ENDED
        )
    assert lobby.state == LobbyPhase.AWAITING_CREATE


# ---------------------------------------------------------------------------
# Promote-vs-recreate decision path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failover_promotes_when_host_still_connected() -> None:
    # Pre-game host drop, but the host is still connected => lossless PROMOTE:
    # epoch bumps, host unchanged, a PromoteHostCommand is published.
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="host-a",
        succession=["host-a", "host-b"],
        host_epoch=0,
        state=LobbyPhase.FILLING,
    )
    session.store[MatchLobby].append(lobby)
    pub = RecordingPublisher()

    result = await trigger_failover(
        session,
        match_id=match_id,
        host_connected=True,
        missed_heartbeats=service.HEARTBEAT_GRACE,
        publisher=pub,
    )
    assert result.action == FailoverAction.PROMOTE
    assert result.host_puuid == "host-a"  # kept
    assert lobby.host_epoch == 1  # bumped
    promote = pub.published[-1].payload
    assert isinstance(promote, PromoteHostCommand)
    assert promote.new_host_epoch == 1
    assert promote.new_host_puuid == "host-a"


@pytest.mark.asyncio
async def test_failover_recreates_when_host_gone() -> None:
    # Host gone (not connected) => RECREATE by the next successor at a new epoch,
    # state reset to AWAITING_CREATE, create+invite re-published.
    session = FakeSession()
    match_id = uuid.uuid4()
    session.store[ClientSession].append(_client_session(match_id, "host-a", host_opt_in=True))
    session.store[ClientSession].append(_client_session(match_id, "host-b", host_opt_in=True))
    lobby = _lobby(
        match_id,
        host_puuid="host-a",
        succession=["host-a", "host-b"],
        host_epoch=0,
        state=LobbyPhase.FILLING,
    )
    session.store[MatchLobby].append(lobby)
    pub = RecordingPublisher()

    result = await trigger_failover(
        session,
        match_id=match_id,
        host_connected=False,
        missed_heartbeats=service.HEARTBEAT_GRACE,
        publisher=pub,
    )
    assert result.action == FailoverAction.RECREATE
    assert lobby.host_puuid == "host-b"  # advanced to successor
    assert lobby.host_epoch == 1
    assert lobby.state == LobbyPhase.AWAITING_CREATE
    types = [e.payload.type for e in pub.published]
    assert types == [
        "create_lobby_command",
        "invite_players_command",
        "auto_accept_command",
    ]
    assert pub.published[0].payload.host_epoch == 1


@pytest.mark.asyncio
async def test_failover_aborts_to_manual_when_no_successor_left() -> None:
    # Host gone and no standby successor remaining => ABORT_TO_MANUAL.
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="host-a",
        succession=["host-a"],
        host_epoch=0,
        state=LobbyPhase.FILLING,
    )
    session.store[MatchLobby].append(lobby)
    pub = RecordingPublisher()

    result = await trigger_failover(
        session,
        match_id=match_id,
        host_connected=False,
        missed_heartbeats=service.HEARTBEAT_GRACE,
        publisher=pub,
    )
    assert result.action == FailoverAction.ABORT_TO_MANUAL
    assert pub.published[-1].payload.type == "abort_command"


@pytest.mark.asyncio
async def test_failover_noop_within_grace_publishes_nothing() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="host-a",
        succession=["host-a", "host-b"],
        host_epoch=0,
        state=LobbyPhase.FILLING,
    )
    session.store[MatchLobby].append(lobby)
    pub = RecordingPublisher()

    result = await trigger_failover(
        session,
        match_id=match_id,
        host_connected=False,
        missed_heartbeats=0,
        publisher=pub,  # still inside grace
    )
    assert result.action == FailoverAction.NOOP_INGAME
    assert lobby.host_epoch == 0  # untouched
    assert pub.published == []  # nothing dispatched


@pytest.mark.asyncio
async def test_failover_in_game_is_hard_noop() -> None:
    session = FakeSession()
    match_id = uuid.uuid4()
    lobby = _lobby(
        match_id,
        host_puuid="host-a",
        succession=["host-a", "host-b"],
        host_epoch=0,
        state=LobbyPhase.IN_GAME,
    )
    session.store[MatchLobby].append(lobby)
    pub = RecordingPublisher()

    result = await trigger_failover(
        session,
        match_id=match_id,
        host_connected=False,
        missed_heartbeats=99,
        publisher=pub,
    )
    assert result.action == FailoverAction.NOOP_INGAME
    assert pub.published == []


# ---------------------------------------------------------------------------
# order_invitees (pure) — the order a custom lobby fills up in
# ---------------------------------------------------------------------------
def _candidate(
    name: str, team: uuid.UUID | None, *, captain: bool = False
) -> service.InviteCandidate:
    return service.InviteCandidate(
        puuid=name, user_id=uuid.uuid4(), team_id=team, is_captain=captain
    )


def test_invite_order_is_own_team_then_rival_captain_then_the_rest() -> None:
    """Ten invites landing in roster order is how people end up in the wrong lobby.

    The host's own side fills first, then the one person on the other team worth
    talking to, then their roster.
    """
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    candidates = [
        _candidate("rival-3", theirs),
        _candidate("mate-1", ours),
        _candidate("rival-captain", theirs, captain=True),
        _candidate("host", ours, captain=True),
        _candidate("mate-2", ours),
        _candidate("rival-4", theirs),
    ]

    ordered = [c.puuid for c in service.order_invitees(candidates, host_puuid="host")]

    assert ordered[0:2] == ["mate-1", "mate-2"], "own side first, in roster order"
    assert ordered[2] == "rival-captain", "the opposing captain before their roster"
    assert ordered[3:] == ["rival-3", "rival-4"]
    assert "host" not in ordered, "you cannot invite yourself"


def test_invite_order_keeps_players_whose_team_is_unknown() -> None:
    """A missing roster row must not quietly cost somebody their game."""
    ours = uuid.uuid4()
    candidates = [
        _candidate("stray", None),
        _candidate("mate", ours),
        _candidate("host", ours, captain=True),
    ]

    ordered = [c.puuid for c in service.order_invitees(candidates, host_puuid="host")]

    assert ordered == ["mate", "stray"], "unknown side goes last, but still goes"


def test_invite_order_survives_a_host_with_no_resolvable_team() -> None:
    """Failover can hand the lobby to someone whose roster row is missing."""
    a, b = uuid.uuid4(), uuid.uuid4()
    candidates = [
        _candidate("host", None),
        _candidate("p-b", b, captain=True),
        _candidate("p-a", a),
    ]

    ordered = [c.puuid for c in service.order_invitees(candidates, host_puuid="host")]

    assert set(ordered) == {"p-a", "p-b"}, "everyone else is still invited"
    assert ordered[0] == "p-b", "captains still lead their side"


@pytest.mark.asyncio
async def test_initialize_lobby_tells_the_humans_the_lobby_exists(monkeypatch) -> None:
    """A created lobby nobody was told about is a match that never starts.

    Every step of the automation is an LCU write, and writes get refused (opted
    out, kill-switch, League not running) or break on a patch. When that happens
    the players need the lobby name and password in hand — publicly for everyone,
    and by DM for the host and captains, who are the ones expected to act.
    """
    session, match_id, succession = _seeded_session(opt_ins=3)
    match = session.store[Match][0]
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    match.team_a, match.team_b = ours, theirs
    session.store[Tournament].append(
        Tournament(id=match.tournament_id, guild_id=_GUILD, name="Summer Cup")
    )

    captain = session.store[ClientSession][0].user_id
    session.store[Team].extend(
        [
            Team(id=ours, guild_id=_GUILD, name="Ours", captain_user_id=captain),
            Team(id=theirs, guild_id=_GUILD, name="Theirs", captain_user_id=uuid.uuid4()),
        ]
    )
    for cs in session.store[ClientSession]:
        session.store[TeamMember].append(
            TeamMember(team_id=ours, user_id=cs.user_id, guild_id=_GUILD)
        )
        session.store[User].append(
            User(id=cs.user_id, discord_user_id=1000 + len(session.store[User]), display_name="P")
        )

    sent: list[Any] = []

    class _Recorder:
        async def publish(self, envelope: Any) -> None:
            sent.append(envelope)

    # The publisher is imported inside the function, so patch it at its source.
    import app.notifications as notifications_module
    import app.realtime.redis_client as redis_module

    monkeypatch.setattr(notifications_module, "RedisNotificationPublisher", lambda *_: _Recorder())
    monkeypatch.setattr(redis_module, "get_redis", lambda: None)

    await initialize_lobby(
        session,
        match_id=match_id,
        lobby_name="LoL-abc123",
        lobby_password="ABC123",
        publisher=RecordingPublisher(),
        server_secret=_SECRET,
    )

    kinds = [e.kind.value for e in sent]
    assert "match_ready" in kinds, "the guild channel gets the name + password"
    assert "lobby_credentials" in kinds, "the host/captains get a DM"

    public = next(e for e in sent if e.kind.value == "match_ready")
    assert public.payload.lobby_name == "LoL-abc123"
    assert public.payload.lobby_password == "ABC123"

    dm = next(e for e in sent if e.kind.value == "lobby_credentials")
    assert dm.payload.discord_user_id, "a DM needs a snowflake to address"
    assert dm.payload.tournament_name == "Summer Cup"


@pytest.mark.asyncio
async def test_a_notification_failure_never_fails_a_created_lobby(monkeypatch) -> None:
    """The lobby is real and the commands are out — telling people is advisory."""
    session, match_id, _ = _seeded_session(opt_ins=2)

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("redis is down")

    import app.notifications as notifications_module

    monkeypatch.setattr(notifications_module, "RedisNotificationPublisher", _boom)

    result = await initialize_lobby(
        session,
        match_id=match_id,
        lobby_name="LoL-x",
        lobby_password="X1",
        publisher=RecordingPublisher(),
        server_secret=_SECRET,
    )

    assert result.state == LobbyPhase.AWAITING_CREATE
