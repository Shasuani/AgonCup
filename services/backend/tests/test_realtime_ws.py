"""Realtime WebSocket hub tests (PLAN.md §3.6.1 / §5 / §7).

Offline by design — NO live WebSocket, NO live Redis (SHARED CONVENTIONS). The
hub's I/O is factored behind two seams (``WebSocketLike`` + ``PubSubLike``) so a
FAKE websocket (scripted inbound frames + a recorded outbound list) and a FAKE
Redis pub/sub (canned channel messages) drive the full lifecycle deterministically.
The tenant session is the same dict-backed ``FakeSession`` family used by
``tests/test_orchestration.py``, extended to honour the ``app.current_guild`` GUC
so the RLS participant check is observable.

Covered (the task's required matrix):

* auth-reject: a bad/missing ``?token=`` closes with 4401 before any data.
* a foreign-guild / non-participant subscribe is rejected (error + close 4401).
* a published command is relayed to the subscribed client (server->client).
* an inbound heartbeat / ack / report routes to the right orchestration ingestion
  function (epoch-fenced server-side; we assert the persisted side effect).
* a malformed frame is skipped, not fatal (the socket keeps serving).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from lol_realtime import (
    AudienceDescriptor,
    AudienceKind,
    CreateLobbyCommand,
    Envelope,
    LobbySpec,
)
from lol_schemas.enums import LobbyPhase, ResultSource

from app.auth.jwt import mint_access_token
from app.models import ClientSession, MatchLobby, ResultReport
from app.realtime import hub

_GUILD = uuid.uuid4()
_USER = uuid.uuid4()
_SECRET = "test-server-secret"


# ---------------------------------------------------------------------------
# Fake tenant AsyncSession (dict-backed; honours the app.current_guild GUC)
# ---------------------------------------------------------------------------
@dataclass
class _Result:
    rows: list[Any]
    rowcount: int = 0

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self.rows

    def __iter__(self) -> Any:
        return iter(self.rows)


class FakeSession:
    """Dict-backed AsyncSession stand-in covering the hub's RLS + ingestion paths."""

    # Shared store so a fresh session per inbound frame still sees the same lobby.
    def __init__(self, store: dict[type, list[Any]]) -> None:
        self.store = store
        self.current_guild: str | None = None

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> Any:
        name = statement.__class__.__name__
        if name == "TextClause":
            sql = str(statement).lower()
            if "set_config" in sql and params and "gid" in params:
                self.current_guild = str(params["gid"])
                return _Result([self.current_guild])
            if "current_setting" in sql:
                return _Result([self.current_guild] if self.current_guild else [None])
            return _Result([])
        return self._execute_select(statement)

    def _execute_select(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        model = _model_of(entity)
        crit = _equality_filters(statement)
        # RLS emulation: a row is only visible when its guild matches the GUC.
        rows = [
            r
            for r in self.store[model]
            if _matches(r, crit) and _guild_visible(r, self.current_guild)
        ]
        return _Result(rows)

    def begin(self) -> _Txn:
        return _Txn()

    def begin_nested(self) -> _Txn:
        return _Txn()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _Txn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _guild_visible(row: Any, current_guild: str | None) -> bool:
    gid = getattr(row, "guild_id", None)
    if gid is None:
        return True
    return current_guild is not None and str(gid) == str(current_guild)


def _model_of(entity: Any) -> type:
    if hasattr(entity, "class_"):
        return entity.class_
    if hasattr(entity, "entity"):
        return entity.entity
    name = getattr(entity, "name", None)
    for model in (ClientSession, MatchLobby):
        if model.__tablename__ == name:
            return model
    return entity


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = getattr(right, "value", right)
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


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    for key, val in crit.items():
        actual = getattr(row, key, None)
        actual = getattr(actual, "value", actual)
        val = getattr(val, "value", val)
        if str(actual) != str(val):
            return False
    return True


# ---------------------------------------------------------------------------
# Fake WebSocket + Fake Redis pub/sub seams
# ---------------------------------------------------------------------------
class _Disconnect(Exception):
    """Raised by the fake socket's ``receive_text`` to signal a clean disconnect."""


@dataclass
class FakeWebSocket:
    """A scripted ``WebSocketLike``: replays inbound frames, records outbound ones."""

    token: str | None
    inbound: list[str]
    sent: list[str] = field(default_factory=list)
    accepted: bool = False
    closed_code: int | None = None
    _cursor: int = 0

    @property
    def query_params(self) -> dict[str, str]:
        return {"token": self.token} if self.token is not None else {}

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        if self._cursor >= len(self.inbound):
            raise _Disconnect
        frame = self.inbound[self._cursor]
        self._cursor += 1
        return frame


@dataclass
class FakePubSub:
    """A ``PubSubLike`` that yields canned channel messages then ends (finite)."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    subscribed: list[str] = field(default_factory=list)
    unsubscribed: list[str] = field(default_factory=list)
    closed: bool = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed.extend(channels)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        for msg in self.messages:
            yield msg

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _token(*, guild_id: uuid.UUID | None = _GUILD, roles: tuple[str, ...] = ()) -> str:
    return mint_access_token(
        user_id=str(_USER),
        guild_id=str(guild_id) if guild_id else None,
        roles=list(roles),
        now=datetime.now(tz=UTC),
    )


def _participant(match_id: uuid.UUID, *, guild_id: uuid.UUID = _GUILD) -> ClientSession:
    cs = ClientSession(
        guild_id=guild_id,
        user_id=_USER,
        puuid="puuid-1",
        match_id=match_id,
        host_opt_in=True,
    )
    cs.id = uuid.uuid4()
    return cs


def _lobby(match_id: uuid.UUID, *, host_epoch: int, state: LobbyPhase) -> MatchLobby:
    lobby = MatchLobby(
        guild_id=_GUILD,
        match_id=match_id,
        host_puuid="puuid-1",
        host_succession=["puuid-1"],
        host_epoch=host_epoch,
        lobby_name="match-lobby",
        lobby_password="pw",
        state=state,
    )
    lobby.id = uuid.uuid4()
    return lobby


def _store(*objects: Any) -> dict[type, list[Any]]:
    store: dict[type, list[Any]] = {ClientSession: [], MatchLobby: [], ResultReport: []}
    for obj in objects:
        store[type(obj)].append(obj)
    return store


def _session_factory(store: dict[type, list[Any]]):  # noqa: ANN202 - test helper
    def factory() -> FakeSession:
        return FakeSession(store)

    return factory


def _command_message(match_id: uuid.UUID) -> dict[str, Any]:
    """A Redis 'message' carrying a serialized orchestration command Envelope."""
    envelope = Envelope(
        origin_guild_id=str(_GUILD),
        audience=AudienceDescriptor(kind=AudienceKind.MATCH, ref=str(match_id)),
        payload=CreateLobbyCommand(
            command_id="cmd-1",
            match_id=str(match_id),
            host_epoch=0,
            spec=LobbySpec(lobby_name="match-lobby", lobby_password="pw"),
        ),
    )
    return {"type": "message", "data": envelope.model_dump_json()}


# ===========================================================================
# AUTH
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_token_closes_4401_before_data() -> None:
    ws = FakeWebSocket(token=None, inbound=[])
    await hub.serve(
        ws,
        session_factory=_session_factory(_store()),
        pubsub_factory=lambda: FakePubSub(),
    )
    assert ws.accepted is True
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert ws.sent == []  # closed before any data


@pytest.mark.asyncio
async def test_bad_token_closes_4401() -> None:
    ws = FakeWebSocket(token="not-a-real-jwt", inbound=[])
    await hub.serve(
        ws,
        session_factory=_session_factory(_store()),
        pubsub_factory=lambda: FakePubSub(),
    )
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert ws.sent == []


# ===========================================================================
# SUBSCRIBE authz / scope
# ===========================================================================
@pytest.mark.asyncio
async def test_subscribe_to_non_participant_match_is_rejected() -> None:
    match_id = uuid.uuid4()
    # No ClientSession roster row for this user/match -> not a participant.
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "subscribe", "matchId": str(match_id)})],
    )
    await hub.serve(
        ws,
        session_factory=_session_factory(_store()),
        pubsub_factory=lambda: FakePubSub(),
    )
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    error = json.loads(ws.sent[-1])
    assert error["type"] == "error"


@pytest.mark.asyncio
async def test_subscribe_to_foreign_guild_match_is_rejected() -> None:
    # The roster row exists but in ANOTHER guild; under the RLS GUC scoped to the
    # token's guild it is invisible -> "not a participant".
    match_id = uuid.uuid4()
    foreign = _participant(match_id, guild_id=uuid.uuid4())
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "subscribe", "matchId": str(match_id)})],
    )
    await hub.serve(
        ws,
        session_factory=_session_factory(_store(foreign)),
        pubsub_factory=lambda: FakePubSub(),
    )
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert json.loads(ws.sent[-1])["type"] == "error"


@pytest.mark.asyncio
async def test_subscribe_participant_confirms_and_uses_match_channel() -> None:
    match_id = uuid.uuid4()
    store = _store(_participant(match_id))
    pubsub = FakePubSub()
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "subscribe", "matchId": str(match_id)})],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    confirm = json.loads(ws.sent[0])
    assert confirm == {"type": "subscribed", "matchId": str(match_id)}
    assert pubsub.subscribed == [hub.command_channel(_GUILD, match_id)]
    assert pubsub.unsubscribed == [hub.command_channel(_GUILD, match_id)]
    assert pubsub.closed is True  # clean teardown


# ===========================================================================
# SUBSCRIBE with an explicit (per-match) guildId — the WS guild-scope fix
# ===========================================================================
@pytest.mark.asyncio
async def test_subscribe_with_matching_guild_id_succeeds() -> None:
    # The subscribe frame carries the match's guildId; the RLS participant check is
    # scoped to THAT guild and the user is a participant there -> subscribed.
    match_id = uuid.uuid4()
    store = _store(_participant(match_id))
    pubsub = FakePubSub()
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id), "guildId": str(_GUILD)})
        ],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    assert json.loads(ws.sent[0]) == {"type": "subscribed", "matchId": str(match_id)}
    assert pubsub.subscribed == [hub.command_channel(_GUILD, match_id)]


@pytest.mark.asyncio
async def test_subscribe_with_identity_only_token_uses_claimed_guild() -> None:
    # An IDENTITY-only token (no gid) can still subscribe to a match in another
    # guild by claiming that guild in the subscribe frame (the multi-guild session).
    other_guild = uuid.uuid4()
    match_id = uuid.uuid4()
    store = _store(_participant(match_id, guild_id=other_guild))
    pubsub = FakePubSub()
    ws = FakeWebSocket(
        token=_token(guild_id=None),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id), "guildId": str(other_guild)})
        ],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    assert json.loads(ws.sent[0]) == {"type": "subscribed", "matchId": str(match_id)}
    assert pubsub.subscribed == [hub.command_channel(other_guild, match_id)]


@pytest.mark.asyncio
async def test_subscribe_with_forged_guild_id_is_fail_closed() -> None:
    # The participant's ClientSession is in _GUILD, but the frame claims a DIFFERENT
    # guild. Under RLS scoped to the forged guild the roster row is invisible ->
    # "not a participant" -> error + 4401 BEFORE any pubsub.subscribe (no leak).
    match_id = uuid.uuid4()
    forged = uuid.uuid4()
    store = _store(_participant(match_id, guild_id=_GUILD))
    pubsub = FakePubSub()
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id), "guildId": str(forged)})
        ],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert json.loads(ws.sent[-1])["type"] == "error"
    assert pubsub.subscribed == []  # never subscribed -> no command-channel leak


@pytest.mark.asyncio
async def test_subscribe_with_invalid_guild_id_is_rejected() -> None:
    match_id = uuid.uuid4()
    store = _store(_participant(match_id))
    pubsub = FakePubSub()
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id), "guildId": "not-a-uuid"})
        ],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert json.loads(ws.sent[-1])["detail"] == "invalid guildId"
    assert pubsub.subscribed == []


# ===========================================================================
# SERVER -> CLIENT command relay
# ===========================================================================
@pytest.mark.asyncio
async def test_published_command_is_relayed_to_subscribed_client() -> None:
    match_id = uuid.uuid4()
    store = _store(_participant(match_id))
    pubsub = FakePubSub(messages=[_command_message(match_id)])
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "subscribe", "matchId": str(match_id)})],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    # First frame is the subscribed confirmation; then the relayed command.
    command_frames = [json.loads(s) for s in ws.sent if json.loads(s).get("type") == "command"]
    assert len(command_frames) == 1
    relayed = command_frames[0]["command"]
    assert relayed["type"] == "create_lobby_command"
    assert relayed["match_id"] == str(match_id)
    # The relayed frame surfaces the match's guildId (from the envelope) so the
    # desktop can thread it back into its subscribe frame (the WS guild-scope fix).
    assert command_frames[0]["guildId"] == str(_GUILD)
    assert relayed["host_epoch"] == 0


# ===========================================================================
# CLIENT -> SERVER ingestion routing (epoch-fenced server-side, reused)
# ===========================================================================
@pytest.mark.asyncio
async def test_inbound_heartbeat_routes_to_ingest_heartbeat() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.FILLING)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            json.dumps(
                {
                    "type": "heartbeat",
                    "heartbeat": {"hostEpoch": 0, "phase": LobbyPhase.FILLING.value},
                }
            ),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )
    # ingest_heartbeat stamps liveness on a current-epoch heartbeat.
    assert lobby.last_host_heartbeat_at is not None


@pytest.mark.asyncio
async def test_inbound_ack_routes_to_ingest_lobby_state_lobby_created() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.AWAITING_CREATE)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            json.dumps(
                {
                    "type": "ack",
                    "ack": {
                        "commandId": "cmd-1",
                        "hostEpoch": 0,
                        "ok": True,
                        "lcuLobbyId": "lcu-9",
                    },
                }
            ),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )
    # ingest_lobby_state advanced AWAITING_CREATE -> LOBBY_OPEN and stored the id.
    assert lobby.state == LobbyPhase.LOBBY_OPEN
    assert lobby.lcu_lobby_id == "lcu-9"


@pytest.mark.asyncio
async def test_inbound_report_routes_to_ingest_lobby_state_game_ended() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.IN_GAME)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            json.dumps({"type": "report", "report": {"hostEpoch": 0}}),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )
    assert lobby.state == LobbyPhase.RESULT_PENDING
    # A phase-only report (no eog block) persists nothing to corroborate.
    assert store[ResultReport] == []


def _eog_report(*, lcu_game_id: str = "game-123", reporter: str = "puuid-A") -> dict[str, Any]:
    """A desktop ``EogReport`` JSON body (camelCase, as it arrives over the wire)."""
    return {
        "source": "eogStatsBlock",
        "lcuGameId": lcu_game_id,
        "reporterPuuid": reporter,
        "teams": [
            {
                "isWinningTeam": True,
                "players": [
                    {"puuid": "puuid-A", "championsKilled": 7, "numDeaths": 2, "assists": 5}
                ],
            },
            {
                "isWinningTeam": False,
                "players": [
                    {"puuid": "puuid-B", "championsKilled": 3, "numDeaths": 6, "assists": 4},
                    {"championsKilled": 1, "numDeaths": 1, "assists": 0},  # no puuid -> skipped
                ],
            },
        ],
        "liveClientEvents": {
            "source": "liveClientData",
            "events": [{"eventId": 1, "eventName": "ChampionKill", "eventTime": 12.3}],
            "schemaVersion": "0.0.0-phase0",
        },
        "schemaVersion": "0.0.0-phase0",
        "capturedAtMs": 1234567890,
    }


@pytest.mark.asyncio
async def test_inbound_report_persists_result_report_row() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.IN_GAME)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            json.dumps({"type": "report", "report": {"hostEpoch": 0, "report": _eog_report()}}),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )

    # Phase advanced AND the forgeable corroboration row was persisted (§3.6.6).
    assert lobby.state == LobbyPhase.RESULT_PENDING
    assert len(store[ResultReport]) == 1
    row = store[ResultReport][0]
    assert row.match_id == match_id
    assert row.guild_id == _GUILD  # tenant-stamped from the authorized subscription.
    assert row.lcu_game_id == "game-123"
    assert row.reporter_puuid == "puuid-A"
    assert row.source == ResultSource.EOG_STATS_BLOCK
    assert row.claimed_winner_team is None  # winner->team mapping is the adjudicator's job.
    # KDA is flattened by PUUID; the player line without a PUUID is dropped.
    assert row.reported_kda == {
        "puuid-A": {"kills": 7, "deaths": 2, "assists": 5},
        "puuid-B": {"kills": 3, "deaths": 6, "assists": 4},
    }
    assert row.kill_stream["events"][0]["eventName"] == "ChampionKill"
    assert row.schema_version == "0.0.0-phase0"
    assert row.raw_payload["lcuGameId"] == "game-123"


@pytest.mark.asyncio
async def test_inbound_report_persist_is_idempotent_first_write_wins() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.IN_GAME)
    store = _store(_participant(match_id), lobby)
    report_frame = json.dumps(
        {"type": "report", "report": {"hostEpoch": 0, "report": _eog_report()}}
    )
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            report_frame,  # durable client queue re-sends the same final report.
            report_frame,
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )

    # Unique on (lcu_game_id, reporter_puuid, source) -> exactly one row, not two.
    assert len(store[ResultReport]) == 1


@pytest.mark.asyncio
async def test_stale_epoch_heartbeat_is_dropped_not_fatal() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=2, state=LobbyPhase.FILLING)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            # Stale epoch (1 < 2) -> service raises StaleEpochError -> dropped.
            json.dumps({"type": "heartbeat", "heartbeat": {"hostEpoch": 1}}),
            # A subsequent CURRENT-epoch heartbeat still works (socket survived).
            json.dumps({"type": "heartbeat", "heartbeat": {"hostEpoch": 2}}),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )
    assert lobby.last_host_heartbeat_at is not None  # the current-epoch one landed


# ===========================================================================
# Resilience: malformed frames are skipped, not fatal
# ===========================================================================
@pytest.mark.asyncio
async def test_malformed_inbound_frame_is_skipped_not_fatal() -> None:
    match_id = uuid.uuid4()
    lobby = _lobby(match_id, host_epoch=0, state=LobbyPhase.FILLING)
    store = _store(_participant(match_id), lobby)
    ws = FakeWebSocket(
        token=_token(),
        inbound=[
            json.dumps({"type": "subscribe", "matchId": str(match_id)}),
            "}{ not json",  # malformed -> skipped
            json.dumps([1, 2, 3]),  # valid JSON but not an object -> skipped
            json.dumps({"type": "heartbeat", "heartbeat": {"hostEpoch": 0}}),
        ],
    )
    await hub.serve(
        ws, session_factory=_session_factory(store), pubsub_factory=lambda: FakePubSub()
    )
    # The socket kept serving past the two malformed frames and ingested the last.
    assert lobby.last_host_heartbeat_at is not None


@pytest.mark.asyncio
async def test_first_frame_must_be_subscribe() -> None:
    match_id = uuid.uuid4()
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "heartbeat", "heartbeat": {"hostEpoch": 0}})],
    )
    await hub.serve(
        ws,
        session_factory=_session_factory(_store(_participant(match_id))),
        pubsub_factory=lambda: FakePubSub(),
    )
    assert ws.closed_code == hub.WS_CLOSE_AUTH
    assert json.loads(ws.sent[-1])["type"] == "error"


@pytest.mark.asyncio
async def test_malformed_redis_command_payload_is_skipped() -> None:
    match_id = uuid.uuid4()
    store = _store(_participant(match_id))
    pubsub = FakePubSub(
        messages=[
            {"type": "message", "data": "not-json"},
            {"type": "subscribe", "data": 1},  # non-message control frame ignored
            _command_message(match_id),
        ]
    )
    ws = FakeWebSocket(
        token=_token(),
        inbound=[json.dumps({"type": "subscribe", "matchId": str(match_id)})],
    )
    await hub.serve(ws, session_factory=_session_factory(store), pubsub_factory=lambda: pubsub)
    command_frames = [json.loads(s) for s in ws.sent if json.loads(s).get("type") == "command"]
    # Only the one well-formed envelope was relayed; the poison frame was skipped.
    assert len(command_frames) == 1
