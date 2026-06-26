"""Realtime WebSocket hub: auth, subscription authz, and command relay (PLAN.md §3.6 / §5).

This module owns the *behaviour* of the realtime WebSocket endpoint, factored
behind small I/O seams so it is exhaustively unit-testable offline (a FAKE
WebSocket + a FAKE Redis pub/sub — NO live socket or Redis, per the SHARED
CONVENTIONS). ``app.realtime.router`` is the thin FastAPI wiring that supplies the
real Starlette ``WebSocket`` and a real ``redis.asyncio`` client.

Wire contract (backend <-> desktop, PLAN.md §3.6.1 / §5)
--------------------------------------------------------
* **AUTH** — the client connects with the access JWT as a ``?token=<jwt>`` query
  param. We verify it via :func:`app.auth.jwt.verify_access_token`; on failure we
  CLOSE with code ``4401`` before any data flows. (Query-param tokens can leak via
  proxy/access logs — acceptable for the v1 loopback/native desktop client; a
  ``Sec-WebSocket-Protocol`` bearer subprotocol is the documented follow-up.)
* **SUBSCRIBE** — after connect the client sends
  ``{"type":"subscribe","matchId":"..."}``. We resolve the principal's acting
  guild, set the ``app.current_guild`` GUC, and validate the principal is a
  *participant* of that match (a ``ClientSession`` roster row under RLS) before
  subscribing the connection to that match's orchestration COMMAND channel. We
  reply ``{"type":"subscribed","matchId":"..."}`` on success, or
  ``{"type":"error","detail":"..."}`` + close on auth/scope failure. A client may
  only subscribe to matches in its own guild (RLS makes a foreign-guild match
  simply invisible -> "not a participant").
* **SERVER->CLIENT** — orchestration publishes :class:`lol_realtime.Envelope`
  command frames onto the per-guild/per-match Redis channel
  (:func:`command_channel`); we relay each as
  ``{"type":"command","command": <LobbyCommand JSON>}`` to the subscribed client.
* **CLIENT->SERVER** — ``{"type":"ack"|"heartbeat"|"report", ...}`` is routed to
  the :mod:`app.orchestration.service` ingestion functions (epoch-fenced
  server-side; we REUSE them, never reimplement the fence).
* **Framing** — JSON text frames; one Redis subscription per connection; a
  malformed frame is logged + skipped, never fatal; clean teardown on disconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from lol_core import LobbyEvent
from lol_schemas.enums import LobbyPhase, ResultSource
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Principal
from app.auth.jwt import InvalidAccessToken, verify_access_token
from app.logging import get_logger
from app.models import ClientSession, ResultReport
from app.orchestration import service

logger = get_logger(__name__)

#: WS close code for an authentication/authorization failure (per the wire
#: contract — a private-use 4xxx code so the client can distinguish auth-reject
#: from a normal close). Used for a bad/missing token and a scope failure.
WS_CLOSE_AUTH = 4401

#: Redis pub/sub channel-name prefix for orchestration COMMANDS on the realtime
#: plane. The orchestration publish seam targets the same namespace so the WS
#: gateway and the publisher agree on the channel (PLAN.md §3.6.1 / §5).
COMMAND_CHANNEL_PREFIX = "lol:rt:cmd"

#: Map an inbound :class:`lol_realtime.LobbyAck`-style frame to the lobby state
#: machine event the ack acknowledges (PLAN.md §3.6.1). A ``lobby_ack`` reports a
#: command outcome; the only state-advancing ack the desktop sends is the
#: lobby-created confirmation, so we drive ``LOBBY_CREATED`` when the client
#: reports a live ``lcuLobbyId``. Anything else is a non-advancing ack (logged).


def command_channel(guild_id: str | uuid.UUID, match_id: str | uuid.UUID) -> str:
    """Return the per-guild/per-match orchestration command channel name.

    Both halves — the orchestration publish seam (see
    :class:`app.realtime.hub.RedisCommandPublisher`) and the WS subscriber here —
    derive the channel from ``(guild_id, match_id)`` so a command published for a
    match is delivered only to that match's subscribers, and never crosses a
    tenant (the guild is part of the key; RLS authz still gates *subscription*).
    """
    return f"{COMMAND_CHANNEL_PREFIX}:{guild_id}:{match_id}"


# ---------------------------------------------------------------------------
# I/O seams (so tests inject a fake WebSocket + fake Redis pub/sub)
# ---------------------------------------------------------------------------
@runtime_checkable
class WebSocketLike(Protocol):
    """The narrow Starlette ``WebSocket`` surface the hub uses (test seam).

    A fake implementing exactly these methods drives the hub with no live socket.
    ``receive_text`` raises to signal disconnect (Starlette raises
    ``WebSocketDisconnect``); the hub treats any exception from it as a clean
    teardown signal.
    """

    @property
    def query_params(self) -> Any:  # mapping-like: supports .get("token")
        ...

    async def accept(self) -> None: ...

    async def close(self, code: int = 1000) -> None: ...

    async def send_text(self, data: str) -> None: ...

    async def receive_text(self) -> str: ...


@runtime_checkable
class PubSubLike(Protocol):
    """The narrow Redis pub/sub surface the hub uses (test seam).

    Mirrors ``redis.asyncio.client.PubSub``: ``subscribe``/``unsubscribe`` and an
    async ``listen()`` yielding ``{"type": "message", "data": <bytes|str>}`` dicts.
    A fake yields canned frames so the relay is tested without Redis.
    """

    async def subscribe(self, *channels: str) -> None: ...

    async def unsubscribe(self, *channels: str) -> None: ...

    def listen(self) -> AsyncIterator[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


#: A factory the router injects: given a channel, return a subscribed pub/sub
#: object. Kept as a callable seam so the router owns the real Redis client's
#: lifecycle and tests pass a fake factory (PLAN.md §3.2 publisher-seam pattern).
PubSubFactory = Callable[[], "PubSubLike"]

#: A factory the router injects to obtain a fresh tenant ``AsyncSession``. Kept as
#: a callable (not a yielded dependency) because a long-lived WS connection opens
#: short transactions per inbound frame rather than one request-scoped session.
SessionFactory = Callable[[], AsyncSession]


# ---------------------------------------------------------------------------
# Orchestration publish seam wired to the SAME channel namespace
# ---------------------------------------------------------------------------
class RedisCommandPublisher(service.CommandPublisher):
    """Production :class:`app.orchestration.service.CommandPublisher` over Redis.

    The orchestration service mints MATCH-audience :class:`lol_realtime.Envelope`
    commands and hands them to its abstract ``CommandPublisher`` seam; this
    subclass serializes each onto the per-guild/per-match Redis channel
    (:func:`command_channel`) that the WS hub subscribes to — closing the loop
    between the HTTP/worker write path and the realtime read path. It accepts an
    already-constructed async Redis client so the connection lifecycle is owned by
    the app (created from ``settings.redis_url``), mirroring
    :class:`app.notifications.service.RedisNotificationPublisher`.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, envelope: Any) -> None:
        """Emit ``envelope`` as JSON on its match's command channel."""
        channel = command_channel(envelope.origin_guild_id, envelope.audience.ref)
        await self._client.publish(channel, envelope.model_dump_json())
        logger.info(
            "realtime.command.publish",
            channel=channel,
            origin_guild_id=envelope.origin_guild_id,
            payload_type=envelope.payload.type,
        )


# ---------------------------------------------------------------------------
# Authentication + subscription authorization
# ---------------------------------------------------------------------------
class SubscriptionError(Exception):
    """A subscribe request failed authz/scope validation (-> error + close)."""


@dataclass(slots=True)
class Subscription:
    """A validated connection<->match binding (the principal is a participant)."""

    match_id: uuid.UUID
    guild_id: uuid.UUID
    channel: str


def authenticate(websocket: WebSocketLike) -> Principal:
    """Verify the ``?token=`` access JWT into a :class:`Principal` (PLAN.md §3.1).

    Raises:
        InvalidAccessToken: the token is missing, expired, tampered, or wrong type.
            The caller closes the socket with :data:`WS_CLOSE_AUTH` (4401).
    """
    token = websocket.query_params.get("token")
    if not token:
        raise InvalidAccessToken("missing token query param")
    claims = verify_access_token(token)
    return Principal(user_id=claims.user_id, guild_id=claims.guild_id, roles=claims.roles)


async def authorize_subscription(
    session: AsyncSession, *, principal: Principal, match_id: uuid.UUID
) -> Subscription:
    """Set the tenant GUC and validate the principal participates in ``match_id``.

    The acting guild comes from the JWT claim (the WS has no path/header guild).
    We set ``app.current_guild`` so RLS scopes the participant lookup to that
    tenant, then require a ``ClientSession`` roster row for this user bound to the
    match. A platform operator is exempt (cross-tenant staff). Because the lookup
    runs under RLS, a foreign-guild match is invisible and surfaces as "not a
    participant" — a client can never subscribe outside its own guild.

    Raises:
        SubscriptionError: no acting guild on the token, or the principal is not a
            participant of the match (foreign guild / non-roster).
    """
    if not principal.guild_id:
        raise SubscriptionError("no acting guild on token")
    guild_id = uuid.UUID(principal.guild_id)

    await session.execute(
        text("SELECT set_config('app.current_guild', :gid, true)"),
        {"gid": str(guild_id)},
    )

    if not principal.is_platform_operator:
        member = (
            await session.execute(
                select(ClientSession.id).where(
                    ClientSession.user_id == principal.user_id,
                    ClientSession.match_id == match_id,
                )
            )
        ).first()
        if member is None:
            raise SubscriptionError("principal is not a participant of the match")

    return Subscription(
        match_id=match_id,
        guild_id=guild_id,
        channel=command_channel(guild_id, match_id),
    )


# ---------------------------------------------------------------------------
# Inbound frame routing -> orchestration ingestion (epoch-fenced server-side)
# ---------------------------------------------------------------------------
async def route_inbound(
    session: AsyncSession, *, subscription: Subscription, frame: dict[str, Any]
) -> None:
    """Route one decoded client->server frame to orchestration ingestion.

    The frame ``type`` selects the ingestion function (PLAN.md §3.6.1); each reuses
    the pure :func:`lol_core.reject_stale_ack` epoch fence inside the service — we
    do NOT reimplement it here. A frame that does not match a known client->server
    type, or that an ingestion function rejects (stale epoch / illegal transition),
    is logged and dropped without crashing the socket.
    """
    kind = frame.get("type")
    if kind == "heartbeat":
        await _route_heartbeat(session, subscription=subscription, frame=frame)
    elif kind == "ack":
        await _route_ack(session, subscription=subscription, frame=frame)
    elif kind == "report":
        await _route_report(session, subscription=subscription, frame=frame)
    else:
        logger.warning("realtime.inbound.unknown_type", type=kind)


async def _route_heartbeat(
    session: AsyncSession, *, subscription: Subscription, frame: dict[str, Any]
) -> None:
    body = frame.get("heartbeat") or {}
    try:
        await service.ingest_heartbeat(
            session,
            match_id=subscription.match_id,
            host_epoch=int(body["hostEpoch"]),
            phase=LobbyPhase(body.get("phase", LobbyPhase.FILLING.value)),
        )
    except (KeyError, ValueError, service.OrchestrationError) as exc:
        logger.warning("realtime.inbound.heartbeat_dropped", error=str(exc))


async def _route_ack(
    session: AsyncSession, *, subscription: Subscription, frame: dict[str, Any]
) -> None:
    body = frame.get("ack") or {}
    # A lobby ack only advances the machine when the host confirms a live lobby
    # (lcuLobbyId present) — that is the LOBBY_CREATED signal. Other acks are
    # non-advancing command confirmations (logged, no transition).
    lcu_lobby_id = body.get("lcuLobbyId")
    if not body.get("ok", False) or not lcu_lobby_id:
        logger.info("realtime.inbound.ack_noop", command_id=body.get("commandId"))
        return
    try:
        await service.ingest_lobby_state(
            session,
            match_id=subscription.match_id,
            host_epoch=int(body["hostEpoch"]),
            event=LobbyEvent.LOBBY_CREATED,
            lcu_lobby_id=str(lcu_lobby_id),
        )
    except (KeyError, ValueError, service.OrchestrationError) as exc:
        logger.warning("realtime.inbound.ack_dropped", error=str(exc))


async def _route_report(
    session: AsyncSession, *, subscription: Subscription, frame: dict[str, Any]
) -> None:
    body = frame.get("report") or {}
    # A result report does two things (PLAN.md §3.6.5 / §3.6.6):
    #   1. advances the lobby phase to GAME_ENDED (epoch-fenced in the service), and
    #   2. persists the forgeable per-reporter corroboration row that the quorum
    #      adjudication (Phase 6) later reads.
    # The two are independent: a stale-epoch report must NOT advance the phase, yet
    # the row is still a valid corroboration input, so we persist it regardless.
    try:
        await service.ingest_lobby_state(
            session,
            match_id=subscription.match_id,
            host_epoch=int(body["hostEpoch"]),
            event=LobbyEvent.GAME_ENDED,
        )
    except (KeyError, ValueError, service.OrchestrationError) as exc:
        logger.warning("realtime.inbound.report_dropped", error=str(exc))
    await _persist_result_report(session, subscription=subscription, body=body)


def _kda_by_puuid(teams: list[Any]) -> dict[str, dict[str, int]]:
    """Flatten the eog teams[].players[] scoreboard into ``{puuid: {k,d,a}}``.

    Players with no PUUID (the LCU line omitted it) are skipped — the PUUID is the
    corroboration join key, so a KDA we cannot attribute is not stored. Non-numeric
    counts coerce to 0 rather than poisoning the row (the payload is forgeable).
    """
    out: dict[str, dict[str, int]] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        for player in team.get("players") or []:
            if not isinstance(player, dict):
                continue
            puuid = player.get("puuid")
            if not puuid:
                continue
            out[str(puuid)] = {
                "kills": _as_int(player.get("championsKilled")),
                "deaths": _as_int(player.get("numDeaths")),
                "assists": _as_int(player.get("assists")),
            }
    return out


def _as_int(value: Any) -> int:
    """Best-effort non-negative int coercion for a forgeable scoreboard count."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _persist_result_report(
    session: AsyncSession, *, subscription: Subscription, body: dict[str, Any]
) -> None:
    """Persist one client's end-of-game report as a ``result_reports`` row (§3.6.6).

    The eog block is ``body["report"]`` (the desktop ``EogReport``); a phase-only
    report (no eog block) is a no-op. Idempotent on
    ``(lcu_game_id, reporter_puuid, source)`` — first-write-wins, since the
    durable client queue may re-send the same final report across reconnects. The
    row is tenant-stamped from the authorized ``subscription.guild_id`` (the RLS
    GUC enforces it on INSERT); reporter identity is advisory/forgeable by design —
    truth is established by cross-client corroboration, never one row.
    """
    eog = body.get("report")
    if not isinstance(eog, dict):
        return  # phase-only report (no eog-stats-block) — nothing to corroborate.
    lcu_game_id = eog.get("lcuGameId")
    reporter_puuid = eog.get("reporterPuuid")
    if not lcu_game_id or not reporter_puuid:
        logger.warning("realtime.inbound.report_unpersisted", reason="missing_join_keys")
        return

    existing = (
        await session.execute(
            select(ResultReport.id).where(
                ResultReport.lcu_game_id == str(lcu_game_id),
                ResultReport.reporter_puuid == str(reporter_puuid),
                ResultReport.source == ResultSource.EOG_STATS_BLOCK,
            )
        )
    ).first()
    if existing is not None:
        return  # already corroborated by an earlier (re)send — first-write-wins.

    live_events = eog.get("liveClientEvents")
    schema_version = eog.get("schemaVersion")
    row = ResultReport(
        match_id=subscription.match_id,
        guild_id=subscription.guild_id,
        lcu_game_id=str(lcu_game_id),
        reporter_puuid=str(reporter_puuid),
        source=ResultSource.EOG_STATS_BLOCK,
        # claimed_winner_team stays NULL here: the eog winner is a player set, not
        # our team UUID; mapping it is the adjudicator's job (Phase 6).
        reported_kda=_kda_by_puuid(eog.get("teams") or []),
        kill_stream=live_events if isinstance(live_events, dict) else None,
        schema_version=str(schema_version) if schema_version else None,
        raw_payload=eog,
    )
    # Insert under a SAVEPOINT so a unique-key race (a concurrent re-send won the
    # row between our SELECT and INSERT) rolls back ONLY this insert — the outer
    # transaction (and the GAME_ENDED phase advance) stays intact.
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as exc:
        logger.info("realtime.inbound.report_duplicate", error=str(exc))


# ---------------------------------------------------------------------------
# Connection driver: the full lifecycle (auth -> subscribe -> relay loop)
# ---------------------------------------------------------------------------
def _decode_frame(raw: str) -> dict[str, Any] | None:
    """Decode a JSON text frame into a dict, or ``None`` if malformed (skip it)."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _command_frame(raw: str | bytes) -> str | None:
    """Wrap a published orchestration envelope as a ``command`` client frame.

    The Redis payload is a serialized :class:`lol_realtime.Envelope`; the wire
    contract delivers the inner command payload as
    ``{"type":"command","command": <command JSON>}``. A malformed Redis payload is
    skipped (returns ``None``) so a poisoned channel message never crashes the
    relay.
    """
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8", errors="replace")
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        return None
    return json.dumps({"type": "command", "command": payload})


async def relay_commands(
    websocket: WebSocketLike, pubsub: PubSubLike, *, subscription: Subscription
) -> None:
    """Relay published commands from the match channel to the client (server->client).

    Drains ``pubsub.listen()`` (already subscribed to ``subscription.channel``) and
    forwards each ``message`` frame as a ``command`` envelope. A non-message frame
    (subscribe confirmation) or a malformed payload is skipped. Returns when the
    pub/sub stream ends (unsubscribe/teardown).
    """
    async for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        frame = _command_frame(message.get("data", ""))
        if frame is None:
            logger.warning("realtime.command.malformed", channel=subscription.channel)
            continue
        await websocket.send_text(frame)


async def serve(
    websocket: WebSocketLike,
    *,
    session_factory: SessionFactory,
    pubsub_factory: PubSubFactory,
) -> None:
    """Drive one WS connection end-to-end (PLAN.md §3.6.1 / §5).

    Lifecycle: accept -> authenticate (4401 on failure) -> await the first
    ``subscribe`` frame -> authorize the subscription (RLS participant check) ->
    subscribe the Redis channel and pump both directions concurrently until
    disconnect. The server->client relay (:func:`relay_commands`) and the
    client->server inbound loop (:func:`_inbound_loop`) run under one
    :class:`asyncio.TaskGroup`; the first to finish (a client disconnect ends the
    inbound loop; an exhausted/torn-down pub/sub ends the relay) cancels the other,
    then the Redis subscription is unsubscribed + closed for a clean teardown.

    The whole driver is deterministic under fakes: a fake pub/sub whose
    ``listen()`` is finite and a fake websocket whose ``receive_text`` eventually
    raises both terminate their loop, so :func:`serve` returns without live I/O.
    """
    await websocket.accept()
    try:
        principal = authenticate(websocket)
    except InvalidAccessToken as exc:
        logger.info("realtime.auth.reject", error=str(exc))
        await websocket.close(code=WS_CLOSE_AUTH)
        return

    # Await the first frame: it MUST be a subscribe.
    try:
        raw = await websocket.receive_text()
    except Exception:  # noqa: BLE001 - any receive failure is a clean disconnect
        return
    frame = _decode_frame(raw)
    if frame is None or frame.get("type") != "subscribe" or not frame.get("matchId"):
        await websocket.send_text(json.dumps({"type": "error", "detail": "expected subscribe"}))
        await websocket.close(code=WS_CLOSE_AUTH)
        return

    try:
        match_id = uuid.UUID(str(frame["matchId"]))
    except ValueError:
        await websocket.send_text(json.dumps({"type": "error", "detail": "invalid matchId"}))
        await websocket.close(code=WS_CLOSE_AUTH)
        return

    try:
        async with session_factory() as session, session.begin():
            subscription = await authorize_subscription(
                session, principal=principal, match_id=match_id
            )
    except SubscriptionError as exc:
        logger.info("realtime.subscribe.reject", error=str(exc))
        await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))
        await websocket.close(code=WS_CLOSE_AUTH)
        return

    pubsub = pubsub_factory()
    await pubsub.subscribe(subscription.channel)
    await websocket.send_text(
        json.dumps({"type": "subscribed", "matchId": str(subscription.match_id)})
    )

    try:
        await _pump(websocket, pubsub, subscription=subscription, session_factory=session_factory)
    finally:
        await pubsub.unsubscribe(subscription.channel)
        await pubsub.aclose()


async def _pump(
    websocket: WebSocketLike,
    pubsub: PubSubLike,
    *,
    subscription: Subscription,
    session_factory: SessionFactory,
) -> None:
    """Run the relay + inbound loops concurrently; the first to finish ends both.

    A client disconnect ends the inbound loop and an exhausted/torn-down pub/sub
    ends the relay; either way the connection is done, so we cancel the sibling
    task rather than wait for it (the socket is closing). Cancellation is swallowed
    so teardown stays clean.
    """
    relay = asyncio.create_task(relay_commands(websocket, pubsub, subscription=subscription))
    inbound = asyncio.create_task(
        _inbound_loop(websocket, subscription=subscription, session_factory=session_factory)
    )
    done, pending = await asyncio.wait({relay, inbound}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc


async def _inbound_loop(
    websocket: WebSocketLike,
    *,
    subscription: Subscription,
    session_factory: SessionFactory,
) -> None:
    """Read client frames until disconnect; route each (malformed -> skipped)."""
    while True:
        try:
            raw = await websocket.receive_text()
        except Exception:  # noqa: BLE001 - disconnect / closed socket -> clean exit
            return
        frame = _decode_frame(raw)
        if frame is None:
            logger.warning("realtime.inbound.malformed")
            continue
        async with session_factory() as session, session.begin():
            subscription_session = session
            await _set_tenant(subscription_session, subscription.guild_id)
            await route_inbound(subscription_session, subscription=subscription, frame=frame)


async def _set_tenant(session: AsyncSession, guild_id: uuid.UUID) -> None:
    """Set the tenant GUC so the per-frame ingestion transaction is RLS-scoped."""
    await session.execute(
        text("SELECT set_config('app.current_guild', :gid, true)"),
        {"gid": str(guild_id)},
    )


__all__ = [
    "COMMAND_CHANNEL_PREFIX",
    "WS_CLOSE_AUTH",
    "PubSubFactory",
    "PubSubLike",
    "RedisCommandPublisher",
    "SessionFactory",
    "Subscription",
    "SubscriptionError",
    "WebSocketLike",
    "authenticate",
    "authorize_subscription",
    "command_channel",
    "relay_commands",
    "route_inbound",
    "serve",
]
