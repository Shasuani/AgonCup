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
    AutoAcceptCommand,
    CreateLobbyCommand,
    Envelope,
    EnvelopePayload,
    InvitePlayersCommand,
    LobbySpec,
    PromoteHostCommand,
)
from lol_schemas.enums import GameMode, LobbyPhase, MatchStatus, TeamMemberStatus
from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.logging import get_logger
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


def _match_label(match: Match) -> str:
    """The bracket label every player-facing surface shows (see readiness)."""
    from app.tournaments.readiness import match_label  # noqa: PLC0415 - avoids a cycle

    return match_label(match)


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
    game_mode: GameMode = GameMode.SUMMONERS_RIFT_5V5,
) -> LobbySpec:
    """Build the immutable, verbatim-re-creatable lobby spec (PLAN.md §3.6.2).

    Backend-owned so any successor host re-creates the lobby identically. The
    custom-game config is derived from ``game_mode``: 5v5 Summoner's Rift
    (``CLASSIC``/map 11/teamSize 5) or a 1v1 ARAM duel (``ARAM``/map 12/teamSize 1).
    """
    if game_mode is GameMode.ARAM_1V1:
        lcu_mode, map_id, team_size = "ARAM", 12, 1
    else:
        lcu_mode, map_id, team_size = "CLASSIC", 11, 5
    return LobbySpec(
        game_mode=lcu_mode,
        map_id=map_id,
        team_size=team_size,
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


def build_auto_accept_command(
    *,
    match_id: uuid.UUID,
    host_epoch: int,
    expected_inviter_puuid: str,
    command_id: str | None = None,
) -> AutoAcceptCommand:
    """Mint a fenced ``AutoAcceptCommand`` for the guests (PLAN.md §3.6.3).

    The invite's other half. The host's client sends nine invites, but until this
    is issued nine people still have to click Accept themselves — most of the
    manual work the autonomous path is supposed to remove. ``expected_inviter_puuid``
    keeps the arming narrow: a guest accepts an invite from **this match's
    designated host** and nobody else.
    """
    return AutoAcceptCommand(
        command_id=command_id or _command_id(),
        match_id=str(match_id),
        host_epoch=host_epoch,
        expected_inviter_puuid=expected_inviter_puuid,
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


@dataclass(frozen=True, slots=True)
class InviteCandidate:
    """One person the host has to pull into the custom lobby."""

    puuid: str
    user_id: uuid.UUID
    #: Which side of the match they play for (``None`` = roster not resolvable).
    team_id: uuid.UUID | None
    is_captain: bool


def order_invitees(
    candidates: Sequence[InviteCandidate], *, host_puuid: str
) -> list[InviteCandidate]:
    """Order the invite list so a lobby fills up in a way humans can follow.

    The host sees ten strangers' invites land in whatever order the roster query
    happened to return, which is exactly when someone accepts into the wrong slot
    or assumes the lobby is for the other match. So the order is deliberate:

    1. **The host's own four teammates** — their side of the lobby fills first.
    2. **The opposing captain** — one person to talk to, already in, before their
       roster arrives.
    3. **The rest of the opposing team.**

    The host is dropped (you cannot invite yourself). Anyone whose team could not
    be resolved goes last rather than being silently dropped — a missing roster
    row must not cost somebody their game. Ties inside each group keep the input
    order, so the result is deterministic for a given roster.
    """
    host = next((c for c in candidates if c.puuid == host_puuid), None)
    host_team = host.team_id if host is not None else None
    others = [c for c in candidates if c.puuid != host_puuid]

    own = [c for c in others if host_team is not None and c.team_id == host_team]
    opposing = [
        c for c in others if c.team_id is not None and (host_team is None or c.team_id != host_team)
    ]
    unknown = [c for c in others if c.team_id is None]

    opposing_captains = [c for c in opposing if c.is_captain]
    opposing_rest = [c for c in opposing if not c.is_captain]
    return [*own, *opposing_captains, *opposing_rest, *unknown]


async def _invite_candidates(
    session: AsyncSession, *, match: Match, sessions: Sequence[ClientSession]
) -> list[InviteCandidate]:
    """Attach each connected client to its side of the match, and flag captains."""
    team_ids = [t for t in (match.team_a, match.team_b) if t is not None]
    if not team_ids:
        return [
            InviteCandidate(puuid=s.puuid, user_id=s.user_id, team_id=None, is_captain=False)
            for s in sessions
        ]

    # Whole rows rather than two columns: a match roster is at most ten people,
    # and it keeps the query inside what the offline fake session can model.
    membership: dict[uuid.UUID, uuid.UUID] = {}
    for member in (
        await session.execute(select(TeamMember).where(TeamMember.team_id.in_(team_ids)))
    ).scalars():
        membership[member.user_id] = member.team_id

    captains = {
        team.captain_user_id
        for team in (await session.execute(select(Team).where(Team.id.in_(team_ids))))
        .scalars()
        .all()
    }

    return [
        InviteCandidate(
            puuid=s.puuid,
            user_id=s.user_id,
            team_id=membership.get(s.user_id),
            is_captain=s.user_id in captains,
        )
        for s in sessions
    ]


async def _summoner_ids_for(session: AsyncSession, *, puuids: Sequence[str]) -> dict[str, str]:
    """PUUID -> per-region numeric summonerId, which is what an LCU invite takes.

    Absent for anyone who has not finished account verification; they are left out
    of the invite payload rather than sent as a null the LCU would reject.
    """
    if not puuids:
        return {}
    rows = (
        await session.execute(select(RiotAccount).where(RiotAccount.puuid.in_(list(puuids))))
    ).scalars()
    return {r.puuid: r.summoner_id for r in rows if r.summoner_id}


async def _game_mode_for_match_id(session: AsyncSession, match_id: uuid.UUID) -> GameMode:
    """Resolve the tournament's game mode for a match id (drives the lobby config).

    RLS-scoped like every other read here; joins match -> tournament and falls back
    to the 5v5 default if either is missing (never raises — the lobby must still be
    creatable on the failover path even if a row is unexpectedly absent).
    """
    mode = (
        await session.execute(
            select(Tournament.game_mode)
            .join(Match, Match.tournament_id == Tournament.id)
            .where(Match.id == match_id)
        )
    ).scalar_one_or_none()
    return mode or GameMode.SUMMONERS_RIFT_5V5


async def _get_lobby(session: AsyncSession, match_id: uuid.UUID) -> MatchLobby:
    lobby = (
        await session.execute(select(MatchLobby).where(MatchLobby.match_id == match_id))
    ).scalar_one_or_none()
    if lobby is None:
        raise LobbyNotFoundError(str(match_id))
    return lobby


async def bind_match_roster(session: AsyncSession, *, match: Match) -> int:
    """Bind both teams' players to this match with a per-match ``ClientSession``.

    This is the join that was missing between "the bracket exists" and "the lobby
    can be built". :func:`initialize_lobby` reads its roster exclusively from
    ``ClientSession`` rows carrying this ``match_id``; nothing else ever set one,
    so every match reached lobby creation with an empty roster and died on
    :class:`NoEligibleHostError` — silently, because auto-init swallows it.

    Each bound row is a **copy of the player's standing row** (the ``match_id IS
    NULL`` record written at lock-in by ``record_client_presence``): same PUUID,
    same host consent. Copying rather than moving is what lets one player be bound
    to matches in two tournaments at once without either match stealing the other's
    roster row.

    A player with no standing row is skipped, not invented: it means they never
    locked in from a client, or have no Riot account to invite. Idempotent — a
    second call adds nobody, so re-running a bracket start is safe.

    Returns the number of rows actually created.
    """
    team_ids = [t for t in (match.team_a, match.team_b) if t is not None]
    if not team_ids:
        return 0

    member_ids = [
        uid
        for (uid,) in (
            await session.execute(
                select(TeamMember.user_id).where(
                    TeamMember.team_id.in_(team_ids),
                    TeamMember.status == TeamMemberStatus.ACTIVE,
                )
            )
        ).all()
    ]
    if not member_ids:
        return 0

    guild_id = await _current_guild(session)
    standing = {
        s.user_id: s
        for s in (
            await session.execute(
                select(ClientSession).where(
                    ClientSession.user_id.in_(member_ids),
                    ClientSession.match_id.is_(None),
                )
            )
        ).scalars()
    }
    already = {
        s.user_id
        for s in (
            await session.execute(select(ClientSession).where(ClientSession.match_id == match.id))
        ).scalars()
    }

    bound = 0
    for user_id in member_ids:
        if user_id in already:
            continue
        source = standing.get(user_id)
        if source is None:
            continue
        session.add(
            ClientSession(
                guild_id=guild_id,
                user_id=user_id,
                puuid=source.puuid,
                match_id=match.id,
                host_opt_in=source.host_opt_in,
                consent_version=source.consent_version,
                consent_at=source.consent_at,
            )
        )
        bound += 1
    await session.flush()
    logger.info(
        "orchestration.bind_match_roster",
        match_id=str(match.id),
        bound=bound,
        roster=len(member_ids),
    )
    return bound


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
    match = await _get_match(session, match_id)  # RLS membership / existence check
    game_mode = await _game_mode_for_match_id(session, match_id)

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

    # Deliberate invite order: the host's own side first, then the opposing
    # captain, then their roster (see `order_invitees`). The summonerId join
    # happens HERE rather than on the host: the client has no way to resolve a
    # PUUID, so leaving the list empty meant the lobby was created and then
    # nobody was invited at all.
    candidates = await _invite_candidates(session, match=match, sessions=sessions)
    ordered = order_invitees(candidates, host_puuid=host)
    summoner_ids = await _summoner_ids_for(session, puuids=[c.puuid for c in ordered])
    invitee_puuids = [c.puuid for c in ordered if c.puuid in summoner_ids]
    invitee_summoner_ids = [summoner_ids[p] for p in invitee_puuids]
    if len(invitee_puuids) != len(ordered):
        logger.warning(
            "orchestration.invite.unresolved_summoner_ids",
            match_id=str(match_id),
            missing=len(ordered) - len(invitee_puuids),
        )

    spec = build_lobby_spec(
        match_id=match_id,
        lobby_name=lobby_name,
        lobby_password=lobby_password,
        invitee_summoner_ids=invitee_summoner_ids,
        invitee_puuids=invitee_puuids,
        game_mode=game_mode,
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
    # Arm the guests to accept the invite the host is about to send. Issued on the
    # same match channel — every client filters by match id already — and fenced to
    # the designated host's PUUID so it can never become "accept anything".
    await publisher.publish(
        wrap_envelope(
            build_auto_accept_command(
                match_id=match_id, host_epoch=lobby.host_epoch, expected_inviter_puuid=host
            ),
            guild_id=guild_id,
            match_id=match_id,
        )
    )

    await _announce_lobby(
        session,
        match=match,
        lobby_name=lobby.lobby_name,
        lobby_password=lobby.lobby_password,
        host_puuid=host,
        guild_id=guild_id,
        candidates=candidates,
    )

    logger.info(
        "orchestration.initialize_lobby",
        match_id=str(match_id),
        host_puuid=host,
        host_epoch=lobby.host_epoch,
        succession_len=len(succession),
    )
    return _init_result(lobby)


async def _announce_lobby(
    session: AsyncSession,
    *,
    match: Match,
    lobby_name: str | None,
    lobby_password: str | None,
    host_puuid: str | None,
    guild_id: uuid.UUID,
    candidates: Sequence[InviteCandidate],
) -> None:
    """Tell the humans the lobby exists, and how to reach it by hand.

    Everything above this point assumes the automation works: the host's client
    creates the lobby and the other nine accept invites. Every step of that is an
    LCU write, and writes are refused (opted out, kill-switch, League not
    running) or simply break on a patch. When that happens the match is ready and
    nobody knows anything — which is the state this closes.

    Two surfaces, deliberately: a public ``match_ready`` embed in the guild
    channel so everyone sees the same name/password, and a DM to the host and
    both captains, who are the ones expected to act. Best-effort throughout — a
    notification failure must never fail a lobby that was created successfully.

    ``host_puuid`` is ``None`` on the **manual-fallback** path: nobody opted their
    client in to hosting, so there is no automation to wait for and the message has
    to say so rather than promise an invite that will never arrive.
    """
    from app.notifications import (
        RedisNotificationPublisher,
        publish_lobby_credentials,
        publish_match_ready,
    )
    from app.realtime.redis_client import get_redis

    if not lobby_name or not lobby_password:
        return

    try:
        names = await _display_names(session, user_ids=[c.user_id for c in candidates])
        host = next((c for c in candidates if c.puuid == host_puuid), None) if host_puuid else None
        tournament_name = await _tournament_name_for_match(session, match=match)
        notifier = RedisNotificationPublisher(get_redis())

        await publish_match_ready(
            notifier,
            origin_guild_id=str(guild_id),
            match_label=_match_label(match),
            host_name=names.get(host.user_id, "the host") if host else "nobody yet",
            lobby_name=lobby_name,
            lobby_password=lobby_password,
        )

        # The host must be able to create the lobby by hand; the captains are who
        # everyone else will ask. Anyone without a Discord id is simply skipped.
        recipients = {c.user_id for c in candidates if c.is_captain}
        if host is not None:
            recipients.add(host.user_id)
        for user_id, discord_user_id in (
            await _discord_ids(session, user_ids=sorted(recipients))
        ).items():
            await publish_lobby_credentials(
                notifier,
                origin_guild_id=str(guild_id),
                discord_user_id=discord_user_id,
                lobby_name=lobby_name,
                lobby_password=lobby_password,
                tournament_name=tournament_name,
                opponent_name=None,
                automated=host is not None,
            )
            logger.info(
                "orchestration.lobby_credentials_sent",
                user_id=str(user_id),
                automated=host is not None,
            )
    except Exception:  # noqa: BLE001 — advisory: never fail a created lobby.
        logger.exception("orchestration.announce_lobby_failed", match_id=str(match.id))


async def announce_manual_lobby(
    session: AsyncSession, *, match_id: uuid.UUID, lobby_name: str, lobby_password: str
) -> None:
    """Hand the match to the players when no client can host it.

    The autonomous path needs somebody who opted their client in to hosting. When
    nobody has, :func:`initialize_lobby` raises and the match used to end there —
    no lobby row, no notification, ten people waiting on a message that was never
    going to come. PLAN.md §3.6.1 is explicit that this case falls back to "the
    manual lobby name + password UI", so it is announced instead of dropped.

    Records a ``MatchLobby`` in the terminal ``HOST_LOST`` phase — the state this
    codebase already uses for "bounded epochs exhausted, manual fallback surfaced"
    (see the worker's ``ABORT_TO_MANUAL``). That row is what makes announcing
    **idempotent**: a caller that re-scans playable matches finds a lobby and stops,
    so nobody is DMed the same credentials twice. It is inert by construction — the
    watchdog's failover UPDATE excludes ``host_lost`` — and per PLAN.md §3.6.1 a
    match that fell back to manual stays manual.
    """
    try:
        guild_id = await _current_guild(session)
        match = await _get_match(session, match_id)
        existing = (
            await session.execute(select(MatchLobby).where(MatchLobby.match_id == match_id))
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.add(
            MatchLobby(
                guild_id=guild_id,
                match_id=match_id,
                host_puuid=None,
                host_succession=[],
                host_epoch=0,
                lobby_name=lobby_name,
                lobby_password=lobby_password,
                state=LobbyPhase.HOST_LOST,
            )
        )
        await session.flush()

        sessions = await _checked_in_sessions(session, match_id)
        candidates = await _invite_candidates(session, match=match, sessions=sessions)
        await _announce_lobby(
            session,
            match=match,
            lobby_name=lobby_name,
            lobby_password=lobby_password,
            host_puuid=None,
            guild_id=guild_id,
            candidates=candidates,
        )
    except Exception:  # noqa: BLE001 — advisory: a missing DM must not fail a match.
        logger.exception("orchestration.announce_manual_lobby_failed", match_id=str(match_id))


async def _display_names(
    session: AsyncSession, *, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    return {
        u.id: u.display_name
        for u in (await session.execute(select(User).where(User.id.in_(list(user_ids)))))
        .scalars()
        .all()
    }


async def _discord_ids(
    session: AsyncSession, *, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """user id -> Discord snowflake (as a string), for the users that have one."""
    if not user_ids:
        return {}
    return {
        u.id: str(u.discord_user_id)
        for u in (await session.execute(select(User).where(User.id.in_(list(user_ids)))))
        .scalars()
        .all()
        if u.discord_user_id
    }


async def _tournament_name_for_match(session: AsyncSession, *, match: Match) -> str:
    tournament = (
        await session.execute(select(Tournament).where(Tournament.id == match.tournament_id))
    ).scalar_one_or_none()
    return tournament.name if tournament is not None else "your tournament"


def _init_result(lobby: MatchLobby) -> InitResult:
    return InitResult(
        lobby_id=str(lobby.id),
        match_id=str(lobby.match_id),
        host_puuid=lobby.host_puuid or "",
        host_epoch=lobby.host_epoch,
        succession=tuple(lobby.host_succession),
        state=lobby.state,
    )


def default_lobby_credentials(match_id: uuid.UUID) -> tuple[str, str]:
    """Backend-owned, per-match lobby name + password (edge-cases §8.1).

    Derived deterministically from the match id so any successor host re-creates the
    lobby verbatim (PLAN.md §3.6.2). The password is the manual-fallback join string
    (invite/auto-accept is the primary path); it is not an auth secret.
    """
    short = match_id.hex[:8]
    return f"LoL-{short}", match_id.hex[:6].upper()


def make_command_publisher() -> CommandPublisher:
    """Production real-time publisher (Redis pub/sub onto the WS plane).

    Deferred imports keep orchestration's module load light and avoid import-order
    coupling with the realtime package (mirrors the orchestration router's seam).
    """
    from app.realtime.hub import RedisCommandPublisher  # noqa: PLC0415
    from app.realtime.redis_client import get_redis  # noqa: PLC0415

    return RedisCommandPublisher(get_redis())


async def auto_initialize_lobby(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    publisher: CommandPublisher | None = None,
    server_secret: str | None = None,
) -> InitResult | None:
    """Best-effort lobby init fired when both teams ready up (edge-cases §8.5).

    Generates the backend-owned lobby credentials and delegates to
    :func:`initialize_lobby`. Returns ``None`` (never raises) when nobody on the
    checked-in roster opted in to host — but the players are handed the lobby by
    hand via :func:`announce_manual_lobby` rather than left waiting. Returning
    ``None`` in silence is what made "nobody opted in to host" indistinguishable
    from "nobody started the match". Idempotent via ``initialize_lobby``.
    """
    name, password = default_lobby_credentials(match_id)
    try:
        return await initialize_lobby(
            session,
            match_id=match_id,
            lobby_name=name,
            lobby_password=password,
            publisher=publisher,
            server_secret=server_secret,
        )
    except NoEligibleHostError:
        logger.info("orchestration.auto_init.no_eligible_host", match_id=str(match_id))
        await announce_manual_lobby(
            session, match_id=match_id, lobby_name=name, lobby_password=password
        )
        return None


async def open_playable_matches(
    session: AsyncSession,
    *,
    tournament_id: uuid.UUID,
    publisher: CommandPublisher | None = None,
) -> int:
    """Bind rosters and open a lobby for every match of ``tournament_id`` that can
    now be played.

    The step between "the bracket exists" and "ten people know where to go", and
    the reason locking in used to lead nowhere: a bracket was drawn and then
    *nothing* happened, because opening a lobby was reachable only through a
    per-match ready-up that no client surfaces.

    ``PENDING`` is the whole selection rule — the bracket engine gives a match that
    status exactly when both sides are known — so this is equally correct called
    after the bracket is drawn and after a result fills the next round's slot.

    Safe to call repeatedly: :func:`bind_match_roster` adds nobody twice and both
    lobby paths no-op once a ``MatchLobby`` exists. Best-effort per match — one
    match that cannot be opened must not hold up the rest of the round.

    Returns the number of matches that reached a lobby (automated or manual).
    """
    matches = list(
        (
            await session.execute(
                select(Match).where(
                    Match.tournament_id == tournament_id,
                    Match.status == MatchStatus.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    opened = 0
    for match in matches:
        try:
            await bind_match_roster(session, match=match)
            await auto_initialize_lobby(session, match_id=match.id, publisher=publisher)
            opened += 1
        except Exception:  # noqa: BLE001 — one bad match must not stall the round.
            logger.exception("orchestration.open_playable_match_failed", match_id=str(match.id))
    logger.info(
        "orchestration.open_playable_matches",
        tournament_id=str(tournament_id),
        pending=len(matches),
        opened=opened,
    )
    return opened


@dataclass(frozen=True)
class MyMatch:
    """One live match the caller is rostered in, as their client needs it.

    Answers two questions a desktop client had no way to ask: *which match should
    I open a realtime channel for*, and *what is happening in it*. Without the
    first, the client only ever subscribed to a match id pasted into an env var,
    so the backend's commands were published to a channel with nobody on it.
    """

    match_id: str
    guild_id: str
    tournament_name: str
    round_label: str
    #: None until a lobby is initialized (the match is drawn but not yet opened).
    lobby_state: str | None
    lobby_name: str | None
    lobby_password: str | None
    #: Display name of the designated host; None on the manual-fallback path,
    #: which is exactly when the client must tell the player to create it.
    host_name: str | None
    #: True when THIS caller's client is the designated host.
    is_host: bool


async def list_my_matches(session: AsyncSession, *, user_id: uuid.UUID) -> list[MyMatch]:
    """The caller's live matches, with whatever is known about each lobby.

    Sourced from the caller's match-bound :class:`ClientSession` rows — the same
    rows :func:`initialize_lobby` builds the lobby from — so a client is told about
    exactly the matches the backend would address commands to, and never about one
    it could not participate in. RLS scopes it to the acting guild.

    Completed matches are excluded: a client has nothing left to do for them, and
    subscribing would keep a channel open for a match that will never speak again.
    """
    rows = list(
        (
            await session.execute(
                select(ClientSession).where(
                    ClientSession.user_id == user_id,
                    ClientSession.match_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    match_ids = [r.match_id for r in rows if r.match_id is not None]
    matches = {
        m.id: m
        for m in (await session.execute(select(Match).where(Match.id.in_(match_ids))))
        .scalars()
        .all()
        if m.status is not MatchStatus.COMPLETED
    }
    if not matches:
        return []

    lobbies = {
        lobby.match_id: lobby
        for lobby in (
            await session.execute(select(MatchLobby).where(MatchLobby.match_id.in_(match_ids)))
        )
        .scalars()
        .all()
    }

    out: list[MyMatch] = []
    for row in rows:
        match = matches.get(row.match_id) if row.match_id is not None else None
        if match is None:
            continue
        lobby = lobbies.get(match.id)
        host_name = None
        if lobby is not None and lobby.host_puuid:
            host_name = await _name_for_puuid(session, puuid=lobby.host_puuid)
        out.append(
            MyMatch(
                match_id=str(match.id),
                guild_id=str(match.guild_id),
                tournament_name=await _tournament_name_for_match(session, match=match),
                round_label=_match_label(match),
                lobby_state=lobby.state.value if lobby is not None else None,
                lobby_name=lobby.lobby_name if lobby is not None else None,
                lobby_password=lobby.lobby_password if lobby is not None else None,
                host_name=host_name,
                is_host=lobby is not None and lobby.host_puuid == row.puuid,
            )
        )
    return out


async def _name_for_puuid(session: AsyncSession, *, puuid: str) -> str | None:
    """Resolve a PUUID to a display name (never expose the PUUID to a client UI)."""
    user_id = (
        await session.execute(select(RiotAccount.user_id).where(RiotAccount.puuid == puuid))
    ).scalar_one_or_none()
    if user_id is None:
        return None
    return (
        await session.execute(select(User.display_name).where(User.id == user_id))
    ).scalar_one_or_none()


async def open_matches_after(
    session: AsyncSession, *, match_id: uuid.UUID, publisher: CommandPublisher | None = None
) -> int:
    """Open whatever became playable once ``match_id`` was decided.

    A recorded result fills the winner into the next round's empty slot, and the
    moment both of that match's slots are filled it flips to ``PENDING`` — playable,
    and needing exactly the same roster binding and lobby as a first-round match
    did. Resolving the tournament from the match keeps the results router from
    having to know the bracket layout.
    """
    tournament_id = (
        await session.execute(select(Match.tournament_id).where(Match.id == match_id))
    ).scalar_one_or_none()
    if tournament_id is None:
        return 0
    return await open_playable_matches(session, tournament_id=tournament_id, publisher=publisher)


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
    # Best-effort global presence (the desktop bell). Publish when the match goes
    # in-game; clear when it ends. A Redis/presence failure never fails the ack.
    await _sync_presence(session, match_id=match_id, state=lobby.state)
    # Best-effort live announcement to the guild's Discord feed (edge-cases §9.2).
    await _announce_phase(session, match_id=match_id, state=lobby.state)
    return IngestResult(accepted=True, host_epoch=lobby.host_epoch, state=lobby.state)


#: Terminal lobby states that should drop a match from the live-presence feed.
_PRESENCE_CLEAR_STATES = frozenset({LobbyPhase.ENDED, LobbyPhase.ABORTED, LobbyPhase.HOST_LOST})

#: Lobby phases worth announcing to the guild's Discord feed (curated to milestones
#: — spectators want "lobby up" + "now live", not every internal edge). Result +
#: ready-up announcements come from their own paths (edge-cases §9.2).
_ANNOUNCE_PHASES: dict[LobbyPhase, tuple[str, str]] = {
    LobbyPhase.LOBBY_OPEN: ("lobby open", "The custom lobby is up — players are joining."),
    LobbyPhase.IN_GAME: ("🔴 now live", "The match has started."),
}


async def _announce_phase(session: AsyncSession, *, match_id: uuid.UUID, state: LobbyPhase) -> None:
    """Publish a live phase announcement to the guild's Discord feed (best-effort).

    Curated to the milestones in :data:`_ANNOUNCE_PHASES`; every failure is swallowed
    so a Redis/notification hiccup never fails the host ack (edge-cases §9.2).
    """
    milestone = _ANNOUNCE_PHASES.get(state)
    if milestone is None:
        return
    try:
        from app.notifications import (  # noqa: PLC0415 - deferred, best-effort
            RedisNotificationPublisher,
            publish_tournament_announced,
        )
        from app.realtime.redis_client import get_redis  # noqa: PLC0415

        match = (
            await session.execute(select(Match).where(Match.id == match_id))
        ).scalar_one_or_none()
        if match is None:
            return
        tour = (
            await session.execute(select(Tournament).where(Tournament.id == match.tournament_id))
        ).scalar_one_or_none()
        headline, detail = milestone
        label = _match_label(match)
        await publish_tournament_announced(
            RedisNotificationPublisher(get_redis()),
            origin_guild_id=str(match.guild_id),
            tournament_name=tour.name if tour is not None else "Tournament",
            headline=f"{label} — {headline}",
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort side effect, never blocks the ack.
        logger.warning(
            "orchestration.announce_phase_failed", match_id=str(match_id), error=str(exc)
        )


async def _sync_presence(session: AsyncSession, *, match_id: uuid.UUID, state: LobbyPhase) -> None:
    """Mirror a lobby state change into the global presence store (best-effort).

    Deferred imports keep the presence/Redis dependency out of the module import
    path; every failure is swallowed inside the presence helpers so orchestration
    is never impacted (PLAN.md §3.2 live presence).
    """
    if state is not LobbyPhase.IN_GAME and state not in _PRESENCE_CLEAR_STATES:
        return
    from app.presence import service as presence_service  # noqa: PLC0415 - deferred, best-effort
    from app.realtime.redis_client import get_redis  # noqa: PLC0415

    redis = get_redis()
    if state is LobbyPhase.IN_GAME:
        await presence_service.refresh_presence_for_match(session, redis, match_id=match_id)
    else:
        await presence_service.clear_presence_for_match(redis, match_id=match_id)


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
    game_mode = await _game_mode_for_match_id(session, lobby.match_id)
    sessions = await _checked_in_sessions(session, lobby.match_id)
    # Resolve summonerIds here exactly as the initial create does. Passing an empty
    # list re-created the lobby and then invited NOBODY — the same failure
    # `initialize_lobby` was already fixed for, left behind on the failover path,
    # where it is worse: the players have already lost one lobby.
    summoner_ids = await _summoner_ids_for(session, puuids=[s.puuid for s in sessions])
    invitee_puuids = [s.puuid for s in sessions if s.puuid in summoner_ids]
    spec = build_lobby_spec(
        match_id=lobby.match_id,
        lobby_name=lobby.lobby_name or "",
        lobby_password=lobby.lobby_password or "",
        invitee_summoner_ids=[summoner_ids[p] for p in invitee_puuids],
        invitee_puuids=invitee_puuids,
        game_mode=game_mode,
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
    # Re-arm the guests for the NEW host. Auto-accept is fenced to a specific
    # inviter PUUID, so guests still armed for the deposed host would refuse the
    # successor's invite — the automation would look alive and accept nothing.
    await publisher.publish(
        wrap_envelope(
            build_auto_accept_command(
                match_id=lobby.match_id,
                host_epoch=lobby.host_epoch,
                expected_inviter_puuid=successor,
            ),
            guild_id=guild_id,
            match_id=lobby.match_id,
        )
    )


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
