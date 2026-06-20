"""Autonomous lobby orchestration service (PLAN.md §3.6.1).

The backend half of the autonomous LCU match-operation loop. This module owns the
``MatchLobby`` row lifecycle and the issuance of orchestration commands onto the
real-time plane, while delegating **every** policy decision to the pure
:mod:`lol_core.orchestration` state machine — there is no business logic
duplicated here. The split is deliberate (PLAN.md §3.6.1):

* :mod:`lol_core.orchestration` decides *what* should happen (succession order,
  legal phase transition, per-phase failover action, epoch fencing). It is pure
  and exhaustively unit-tested.
* This service decides *how it is persisted and dispatched*: it reads/writes the
  tenant-scoped ``MatchLobby`` row under RLS, mints the ``lol_realtime`` command
  envelopes, and hands them to a thin :class:`CommandPublisher` seam (the Redis
  pub/sub wiring is documented but injectable so unit tests stay offline).

Host-epoch lease (PLAN.md §3.6.1)
---------------------------------
``host_epoch`` is a backend-owned **monotonic lease**. Every (re-)designation of a
host bumps it. A re-designation is applied as a *conditional* UPDATE
``WHERE match_id = :match_id AND host_epoch = :expected`` so it is idempotent and
safe under concurrency: a second worker that observed the same stale epoch and
also tries to bump it finds zero rows affected (someone already advanced the
lease) and backs off rather than double-incrementing. Inbound acks/heartbeats
carrying a stale epoch are rejected via the pure
:func:`lol_core.reject_stale_ack` fence so a deposed/flapping host cannot
split-brain the lobby.

Server seed (PLAN.md §3.6.1)
----------------------------
:func:`seed_host_succession` requires a stable, *secret*, per-match seed so the
order is both deterministic (an idle worker can recompute the next successor) and
unsteerable (a player cannot grind their ``puuid`` against a known seed). We derive
it as ``HMAC(server_secret, match_id)`` from the deployment ``jwt_secret`` (the
already-managed server secret) mixed with the match id — see
:func:`derive_server_seed`.

Tenant scoping
--------------
All DB access is on the request's tenant session; RLS scopes every row to the
acting guild and the ``app.current_guild`` GUC supplies the value for inserts
(read back via :func:`_current_guild`, mirroring ``app.tournaments.service``).
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

from lol_core import (
    Decision,
    FailoverAction,
    IllegalTransitionError,
    LobbyEvent,
    RosterMember,
    decide_failover,
    decide_transition,
    next_successor,
    reject_stale_ack,
    seed_host_succession,
)
from lol_realtime import (
    AbortCommand,
    AudienceDescriptor,
    AudienceKind,
    CreateLobbyCommand,
    Envelope,
    EnvelopePayload,
    InvitePlayersCommand,
    LobbySpec,
    PromoteHostCommand,
)
from lol_schemas.enums import LobbyPhase
from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.logging import get_logger
from app.models import ClientSession, Match, MatchLobby

logger = get_logger(__name__)

#: Bounded re-designation budget before falling back to the manual lobby UI
#: (PLAN.md §3.6.1: "bounded epochs (~2-3)").
MAX_EPOCHS = 3

#: Missed-heartbeat grace window before a pre-game host drop triggers failover.
HEARTBEAT_GRACE = 3


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class OrchestrationError(Exception):
    """Base class for orchestration service failures."""


class MatchNotFoundError(OrchestrationError):
    """No match with that id in the acting guild."""


class LobbyNotFoundError(OrchestrationError):
    """No ``MatchLobby`` for that match in the acting guild."""


class NoEligibleHostError(OrchestrationError):
    """No checked-in roster member opted in to host."""


class StaleEpochError(OrchestrationError):
    """An inbound ack/heartbeat carried a stale (non-current) host epoch."""


class InvalidStateError(OrchestrationError):
    """The lobby is not in a state that permits the requested transition."""


# ---------------------------------------------------------------------------
# Pure helpers (no DB — exhaustively unit-tested)
# ---------------------------------------------------------------------------
def derive_server_seed(match_id: uuid.UUID, *, server_secret: str) -> str:
    """Derive the per-match succession seed (PLAN.md §3.6.1).

    ``seed_host_succession`` needs a stable, secret, per-match seed: stable so the
    order is reproducible by any worker, secret so a player cannot grind their
    ``puuid`` against it. We bind the deployment ``server_secret`` to the match id
    with HMAC-SHA256 so neither alone is sufficient and the result is unsteerable.
    """
    return hmac.new(server_secret.encode(), str(match_id).encode(), sha256).hexdigest()


def roster_from_sessions(sessions: Sequence[ClientSession]) -> list[RosterMember]:
    """Map checked-in client sessions to pure :class:`RosterMember` inputs.

    Only ``host_opt_in`` flows into eligibility; ``seed_host_succession`` filters
    opted-out members itself, but mapping them through keeps the roster binding
    explicit (PLAN.md §3.6.1: opted-out members are *skipped, never commandeered*).
    """
    return [RosterMember(puuid=s.puuid, host_opt_in=bool(s.host_opt_in)) for s in sessions]


def build_lobby_spec(
    *,
    match_id: uuid.UUID,
    lobby_name: str,
    lobby_password: str,
    invitee_summoner_ids: Sequence[str],
    invitee_puuids: Sequence[str],
) -> LobbySpec:
    """Build the immutable, verbatim-re-creatable lobby spec (PLAN.md §3.6.2).

    Backend-owned so any successor host re-creates the lobby identically. The
    canonical custom-game config (``CLASSIC``/map 11/5v5) comes from the
    ``LobbySpec`` defaults.
    """
    return LobbySpec(
        lobby_name=lobby_name,
        lobby_password=lobby_password,
        invitee_summoner_ids=list(invitee_summoner_ids),
        invitee_puuids=list(invitee_puuids),
    )


def build_create_lobby_command(
    *, match_id: uuid.UUID, host_epoch: int, spec: LobbySpec, command_id: str | None = None
) -> CreateLobbyCommand:
    """Mint a fenced ``CreateLobbyCommand`` (PLAN.md §3.6.2)."""
    return CreateLobbyCommand(
        command_id=command_id or _command_id(),
        match_id=str(match_id),
        host_epoch=host_epoch,
        spec=spec,
    )


def build_invite_command(
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    to_summoner_ids: Sequence[str],
    to_puuids: Sequence[str],
    command_id: str | None = None,
) -> InvitePlayersCommand:
    """Mint a fenced batched ``InvitePlayersCommand`` (PLAN.md §3.6.3)."""
    return InvitePlayersCommand(
        command_id=command_id or _command_id(),
        match_id=str(match_id),
        host_epoch=host_epoch,
        to_summoner_ids=list(to_summoner_ids),
        to_puuids=list(to_puuids),
    )


def build_promote_command(
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    new_host_epoch: int,
    new_host_puuid: str,
    command_id: str | None = None,
) -> PromoteHostCommand:
    """Mint a fenced ``PromoteHostCommand`` for lossless failover (PLAN.md §3.6.1)."""
    return PromoteHostCommand(
        command_id=command_id or _command_id(),
        match_id=str(match_id),
        host_epoch=host_epoch,
        new_host_epoch=new_host_epoch,
        new_host_puuid=new_host_puuid,
    )


def build_abort_command(
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    reason: str | None = None,
    command_id: str | None = None,
) -> AbortCommand:
    """Mint a fenced ``AbortCommand`` (tear-down / manual fallback, PLAN.md §3.6.1)."""
    return AbortCommand(
        command_id=command_id or _command_id(),
        match_id=str(match_id),
        host_epoch=host_epoch,
        reason=reason,
    )


def wrap_envelope(
    payload: EnvelopePayload, *, guild_id: uuid.UUID, match_id: uuid.UUID
) -> Envelope:
    """Wrap a command payload in a MATCH-audience :class:`Envelope` (PLAN.md §5).

    ``origin_guild_id`` is present on every message for RLS authz; the audience
    targets all participants of the match.
    """
    return Envelope(
        origin_guild_id=str(guild_id),
        audience=AudienceDescriptor(kind=AudienceKind.MATCH, ref=str(match_id)),
        payload=payload,
    )


def _command_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Command publish seam (Redis pub/sub is a documented wiring point)
# ---------------------------------------------------------------------------
class CommandPublisher:
    """Thin publish abstraction onto the real-time (WS) plane (PLAN.md §3.6.1/§5).

    The concrete transport is Redis pub/sub: the WS gateway subscribes to a
    per-guild/per-match channel and fans the envelope out to the targeted desktop
    clients. That wiring is a **documented seam** — this base class is a no-op sink
    so the orchestration service is testable offline; a production subclass
    overrides :meth:`publish` to ``redis.publish(channel, envelope.model_dump_json())``.
    Inject one via the service entry points.
    """

    async def publish(self, envelope: Envelope) -> None:
        """Publish one envelope onto the real-time plane. Override to wire Redis."""
        logger.info(
            "orchestration.publish",
            origin_guild_id=envelope.origin_guild_id,
            audience=envelope.audience.kind.value,
            ref=envelope.audience.ref,
            payload_type=envelope.payload.type,
        )


class RecordingPublisher(CommandPublisher):
    """A :class:`CommandPublisher` that records published envelopes (tests/debug)."""

    def __init__(self) -> None:
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope) -> None:
        self.published.append(envelope)


# ---------------------------------------------------------------------------
# DB helpers (tenant-scoped via RLS)
# ---------------------------------------------------------------------------
async def _current_guild(session: AsyncSession) -> uuid.UUID:
    """Read the acting guild from the transaction GUC (set by get_tenant_session)."""
    gid = (
        await session.execute(text("SELECT current_setting('app.current_guild', true)"))
    ).scalar_one_or_none()
    if not gid:
        raise InvalidStateError("no acting guild on the session")
    return uuid.UUID(gid)


async def _get_match(session: AsyncSession, match_id: uuid.UUID) -> Match:
    match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    return match


async def _get_lobby(session: AsyncSession, match_id: uuid.UUID) -> MatchLobby:
    lobby = (
        await session.execute(select(MatchLobby).where(MatchLobby.match_id == match_id))
    ).scalar_one_or_none()
    if lobby is None:
        raise LobbyNotFoundError(str(match_id))
    return lobby


async def _checked_in_sessions(session: AsyncSession, match_id: uuid.UUID) -> list[ClientSession]:
    """Checked-in roster for a match: client sessions bound to the match (RLS-scoped)."""
    rows = (
        await session.execute(
            select(ClientSession)
            .where(ClientSession.match_id == match_id)
            .order_by(ClientSession.id)
        )
    ).scalars()
    return list(rows)


# ---------------------------------------------------------------------------
# Lifecycle: create / initialize a MatchLobby (PLAN.md §3.6.1)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class InitResult:
    """Outcome of :func:`initialize_lobby`."""

    lobby_id: str
    match_id: str
    host_puuid: str
    host_epoch: int
    succession: tuple[str, ...]
    state: LobbyPhase


async def initialize_lobby(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    lobby_name: str,
    lobby_password: str,
    publisher: CommandPublisher | None = None,
    server_secret: str | None = None,
) -> InitResult:
    """Create + initialize a ``MatchLobby`` for a match (PLAN.md §3.6.1).

    Computes the deterministic, server-seeded host succession from the checked-in
    roster (host first), leases epoch 0 to the head of the chain, persists the
    lobby in ``ASSIGNED_HOST``, advances it to ``AWAITING_CREATE`` via the pure
    state machine, and issues the ``CreateLobby`` + ``Invite`` commands onto the
    real-time plane. Idempotent: a second call returns the existing lobby unchanged.

    Raises:
        MatchNotFoundError: the match is not in the acting guild.
        NoEligibleHostError: nobody on the checked-in roster opted in to host.
    """
    publisher = publisher or CommandPublisher()
    server_secret = server_secret or get_settings().jwt_secret
    guild_id = await _current_guild(session)
    await _get_match(session, match_id)  # RLS membership / existence check

    existing = (
        await session.execute(select(MatchLobby).where(MatchLobby.match_id == match_id))
    ).scalar_one_or_none()
    if existing is not None:
        return _init_result(existing)

    sessions = await _checked_in_sessions(session, match_id)
    roster = roster_from_sessions(sessions)
    server_seed = derive_server_seed(match_id, server_secret=server_secret)
    succession = seed_host_succession(roster, server_seed=server_seed)
    host = next_successor(succession, current_host=None)
    if host is None:
        raise NoEligibleHostError(str(match_id))

    # Epoch 0 leased to the head of the succession (ASSIGNED_HOST).
    lobby = MatchLobby(
        guild_id=guild_id,
        match_id=match_id,
        host_puuid=host,
        host_succession=list(succession),
        host_epoch=0,
        lobby_name=lobby_name,
        lobby_password=lobby_password,
        state=LobbyPhase.ASSIGNED_HOST,
    )
    session.add(lobby)
    await session.flush()

    # Advance ASSIGNED_HOST -> AWAITING_CREATE through the pure state machine, then
    # issue the create + invite commands (the host acks LOBBY_CREATED later).
    lobby.state = decide_transition(lobby.state, LobbyEvent.HOST_DESIGNATED)
    await session.flush()

    spec = build_lobby_spec(
        match_id=match_id,
        lobby_name=lobby_name,
        lobby_password=lobby_password,
        # ClientSession carries only puuid; summonerId resolution (RiotAccount) is
        # a downstream join the host performs — puuid is the collision-immune key.
        invitee_summoner_ids=[],
        invitee_puuids=[s.puuid for s in sessions],
    )
    create_cmd = build_create_lobby_command(
        match_id=match_id, host_epoch=lobby.host_epoch, spec=spec
    )
    invite_cmd = build_invite_command(
        match_id=match_id,
        host_epoch=lobby.host_epoch,
        to_summoner_ids=spec.invitee_summoner_ids,
        to_puuids=spec.invitee_puuids,
    )
    await publisher.publish(wrap_envelope(create_cmd, guild_id=guild_id, match_id=match_id))
    await publisher.publish(wrap_envelope(invite_cmd, guild_id=guild_id, match_id=match_id))

    logger.info(
        "orchestration.initialize_lobby",
        match_id=str(match_id),
        host_puuid=host,
        host_epoch=lobby.host_epoch,
        succession_len=len(succession),
    )
    return _init_result(lobby)


def _init_result(lobby: MatchLobby) -> InitResult:
    return InitResult(
        lobby_id=str(lobby.id),
        match_id=str(lobby.match_id),
        host_puuid=lobby.host_puuid or "",
        host_epoch=lobby.host_epoch,
        succession=tuple(lobby.host_succession),
        state=lobby.state,
    )


# ---------------------------------------------------------------------------
# Idempotent host re-designation: conditional UPDATE WHERE expected epoch
# ---------------------------------------------------------------------------
async def redesignate_host(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    expected_epoch: int,
    new_host_puuid: str,
    new_state: LobbyPhase | None = None,
) -> bool:
    """Bump the host lease via a conditional UPDATE (PLAN.md §3.6.1).

    Idempotent + concurrency-safe host re-designation: a single
    ``UPDATE ... WHERE match_id = :match_id AND host_epoch = :expected`` is the
    atomic fence. If two workers both observed ``expected_epoch`` and race, exactly
    one row is matched (``rowcount == 1``); the loser sees ``rowcount == 0`` because
    the lease already advanced, and must NOT retry — re-reading would just observe
    the winner's new epoch. The new epoch is always ``expected + 1`` (monotonic),
    so a deposed host's stale-epoch commands are fenced out by
    :func:`lol_core.is_epoch_current`.

    Returns ``True`` iff this call won the conditional update (it advanced the
    lease); ``False`` if another writer already advanced past ``expected_epoch``.
    """
    new_epoch = expected_epoch + 1
    values: dict[str, object] = {"host_epoch": new_epoch, "host_puuid": new_host_puuid}
    if new_state is not None:
        values["state"] = new_state
    result = await session.execute(
        update(MatchLobby)
        .where(MatchLobby.match_id == match_id, MatchLobby.host_epoch == expected_epoch)
        .values(**values)
    )
    # DML execute returns a CursorResult whose rowcount is the matched/updated count
    # (the AsyncSession.execute return type is the broader Result; narrow for mypy).
    won = (cast("CursorResult[Any]", result).rowcount or 0) == 1
    await session.flush()
    logger.info(
        "orchestration.redesignate_host",
        match_id=str(match_id),
        expected_epoch=expected_epoch,
        new_epoch=new_epoch,
        new_host_puuid=new_host_puuid,
        won=won,
    )
    return won


# ---------------------------------------------------------------------------
# Authenticated client ingestion: heartbeat / lobby-state ack / failover
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class IngestResult:
    """Outcome of an ingestion call (heartbeat / ack)."""

    accepted: bool
    host_epoch: int
    state: LobbyPhase
    detail: str = ""


async def ingest_heartbeat(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    phase: LobbyPhase,
) -> IngestResult:
    """Ingest a pre-game host heartbeat, fencing stale epochs (PLAN.md §3.6.1).

    A heartbeat carrying a non-current epoch is a deposed/flapping host
    (split-brain) and is rejected via the pure :func:`lol_core.reject_stale_ack`
    fence — it must not refresh liveness. A current-epoch heartbeat stamps
    ``last_host_heartbeat_at`` so the watchdog's grace window resets.
    """
    lobby = await _get_lobby(session, match_id)
    if reject_stale_ack(host_epoch, lobby.host_epoch):
        logger.warning(
            "orchestration.heartbeat.stale_epoch",
            match_id=str(match_id),
            ack_epoch=host_epoch,
            current_epoch=lobby.host_epoch,
        )
        raise StaleEpochError(f"stale host_epoch {host_epoch} (current {lobby.host_epoch})")
    lobby.last_host_heartbeat_at = _now(session)
    await session.flush()
    return IngestResult(accepted=True, host_epoch=lobby.host_epoch, state=lobby.state)


async def ingest_lobby_state(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    event: LobbyEvent,
    lcu_lobby_id: str | None = None,
) -> IngestResult:
    """Ingest a host's lobby-state ack and advance the phase (PLAN.md §3.6.1).

    Rejects a stale ``host_epoch`` via the pure fence (a deposed host cannot
    advance the machine), then persists the transition through the pure
    :func:`lol_core.decide_transition` — no transition logic is duplicated here;
    an illegal edge surfaces as :class:`InvalidStateError`.
    """
    lobby = await _get_lobby(session, match_id)
    if reject_stale_ack(host_epoch, lobby.host_epoch):
        logger.warning(
            "orchestration.ack.stale_epoch",
            match_id=str(match_id),
            ack_epoch=host_epoch,
            current_epoch=lobby.host_epoch,
        )
        raise StaleEpochError(f"stale host_epoch {host_epoch} (current {lobby.host_epoch})")
    try:
        lobby.state = decide_transition(lobby.state, event)
    except IllegalTransitionError as exc:
        raise InvalidStateError(str(exc)) from exc
    if lcu_lobby_id is not None:
        lobby.lcu_lobby_id = lcu_lobby_id
    await session.flush()
    logger.info(
        "orchestration.ingest_lobby_state",
        match_id=str(match_id),
        lobby_event=event.value,
        state=lobby.state.value,
    )
    return IngestResult(accepted=True, host_epoch=lobby.host_epoch, state=lobby.state)


@dataclass(slots=True)
class FailoverResult:
    """Outcome of :func:`trigger_failover`."""

    action: FailoverAction
    host_epoch: int
    host_puuid: str | None
    state: LobbyPhase
    reason: str


async def trigger_failover(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    host_connected: bool,
    missed_heartbeats: int,
    epochs_used: int | None = None,
    gameflow_advanced: bool = False,
    publisher: CommandPublisher | None = None,
) -> FailoverResult:
    """Resolve + apply a host-drop failover (PLAN.md §3.6.1).

    The *decision* (promote / recreate / no-op / abort-to-manual) is delegated
    wholesale to the pure :func:`lol_core.decide_failover`; this function only
    persists it (via the conditional-update host re-designation for the
    promote/recreate paths) and dispatches the matching command envelope:

    * ``PROMOTE`` — lossless: bump the lease, keep the current host, emit a
      ``PromoteHostCommand`` at the new epoch.
    * ``RECREATE`` — host gone: bump the lease to the next opted-in successor and
      re-issue the ``CreateLobby`` + ``Invite`` commands at the new epoch.
    * ``ABORT_TO_MANUAL`` — bounded epochs exhausted / no standby: emit an
      ``AbortCommand`` so clients fall back to the manual lobby name+password UI.
    * ``NOOP_INGAME`` — benign sentinel (host healthy / within grace / phase
      advanced / in-game / terminal): nothing is persisted or dispatched.

    ``epochs_used`` defaults to the lobby's current ``host_epoch`` (each epoch is
    one consumed re-designation).
    """
    publisher = publisher or CommandPublisher()
    guild_id = await _current_guild(session)
    lobby = await _get_lobby(session, match_id)
    used = lobby.host_epoch if epochs_used is None else epochs_used

    decision: Decision = decide_failover(
        lobby.state,
        host_connected=host_connected,
        missed_heartbeats=missed_heartbeats,
        grace=HEARTBEAT_GRACE,
        epochs_used=used,
        max_epochs=MAX_EPOCHS,
        current_epoch=lobby.host_epoch,
        succession=tuple(lobby.host_succession),
        current_host=lobby.host_puuid,
        gameflow_advanced=gameflow_advanced,
    )

    if decision.action == FailoverAction.PROMOTE:
        await _apply_promote(session, lobby, decision, guild_id=guild_id, publisher=publisher)
    elif decision.action == FailoverAction.RECREATE:
        await _apply_recreate(session, lobby, decision, guild_id=guild_id, publisher=publisher)
    elif decision.action == FailoverAction.ABORT_TO_MANUAL:
        await _apply_abort(session, lobby, decision, guild_id=guild_id, publisher=publisher)
    # NOOP_INGAME: nothing to persist or dispatch.

    logger.info(
        "orchestration.trigger_failover",
        match_id=str(match_id),
        action=decision.action.value,
        reason=decision.reason,
        host_epoch=lobby.host_epoch,
    )
    return FailoverResult(
        action=decision.action,
        host_epoch=lobby.host_epoch,
        host_puuid=lobby.host_puuid,
        state=lobby.state,
        reason=decision.reason,
    )


async def _apply_promote(
    session: AsyncSession,
    lobby: MatchLobby,
    decision: Decision,
    *,
    guild_id: uuid.UUID,
    publisher: CommandPublisher,
) -> None:
    expected = lobby.host_epoch
    new_host = decision.successor_puuid or lobby.host_puuid
    assert new_host is not None  # PROMOTE keeps the (connected) current host
    won = await redesignate_host(
        session,
        match_id=lobby.match_id,
        expected_epoch=expected,
        new_host_puuid=new_host,
    )
    if not won:
        return  # another writer already advanced the lease; back off
    await session.refresh(lobby)
    cmd = build_promote_command(
        match_id=lobby.match_id,
        host_epoch=expected,
        new_host_epoch=lobby.host_epoch,
        new_host_puuid=new_host,
    )
    await publisher.publish(wrap_envelope(cmd, guild_id=guild_id, match_id=lobby.match_id))


async def _apply_recreate(
    session: AsyncSession,
    lobby: MatchLobby,
    decision: Decision,
    *,
    guild_id: uuid.UUID,
    publisher: CommandPublisher,
) -> None:
    expected = lobby.host_epoch
    successor = decision.successor_puuid
    assert successor is not None  # RECREATE always carries the next successor
    won = await redesignate_host(
        session,
        match_id=lobby.match_id,
        expected_epoch=expected,
        new_host_puuid=successor,
        new_state=LobbyPhase.AWAITING_CREATE,
    )
    if not won:
        return
    await session.refresh(lobby)
    sessions = await _checked_in_sessions(session, lobby.match_id)
    spec = build_lobby_spec(
        match_id=lobby.match_id,
        lobby_name=lobby.lobby_name or "",
        lobby_password=lobby.lobby_password or "",
        invitee_summoner_ids=[],
        invitee_puuids=[s.puuid for s in sessions],
    )
    create_cmd = build_create_lobby_command(
        match_id=lobby.match_id, host_epoch=lobby.host_epoch, spec=spec
    )
    invite_cmd = build_invite_command(
        match_id=lobby.match_id,
        host_epoch=lobby.host_epoch,
        to_summoner_ids=spec.invitee_summoner_ids,
        to_puuids=spec.invitee_puuids,
    )
    await publisher.publish(wrap_envelope(create_cmd, guild_id=guild_id, match_id=lobby.match_id))
    await publisher.publish(wrap_envelope(invite_cmd, guild_id=guild_id, match_id=lobby.match_id))


async def _apply_abort(
    session: AsyncSession,
    lobby: MatchLobby,
    decision: Decision,
    *,
    guild_id: uuid.UUID,
    publisher: CommandPublisher,
) -> None:
    cmd = build_abort_command(
        match_id=lobby.match_id, host_epoch=lobby.host_epoch, reason=decision.reason
    )
    await publisher.publish(wrap_envelope(cmd, guild_id=guild_id, match_id=lobby.match_id))


def _now(session: AsyncSession) -> ColumnElement[Any]:
    """Server-side ``now()`` so the heartbeat timestamp is DB-authoritative."""
    return func.now()
