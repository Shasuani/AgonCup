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
from lol_realtime import CreateLobbyCommand, PromoteHostCommand
from lol_schemas.enums import LobbyPhase

from app.models import ClientSession, Match, MatchLobby
from app.orchestration import service
from app.orchestration.service import (
    RecordingPublisher,
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
        rows = [r for r in self.store[model] if _matches(r, crit)]
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
    for model in (Match, MatchLobby, ClientSession):
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
        if str(actual) != str(val):
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

    # CreateLobby + Invite envelopes were published, both fenced at epoch 0.
    payload_types = [e.payload.type for e in pub.published]
    assert payload_types == ["create_lobby_command", "invite_players_command"]
    create = pub.published[0].payload
    assert isinstance(create, CreateLobbyCommand)
    assert create.host_epoch == 0
    assert set(create.spec.invitee_puuids) == set(succession)


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
    assert types == ["create_lobby_command", "invite_players_command"]
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
