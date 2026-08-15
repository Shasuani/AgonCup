"""WebSocket envelope Pydantic models + JSON-schema export.

Every message on the WS plane is an :class:`Envelope`. It carries the tenant of
origin (``origin_guild_id``), an :class:`AudienceDescriptor` (who it targets),
and a discriminated ``payload`` that is either a backend->client **command** or
a client->backend **ack**.

This is the canonical real-time contract (PLAN.md §1.4 / §5). The model set is
the source of truth; :func:`export_json_schema` dumps the schema that
``make gen-clients`` consumes and CI drift-checks.

Phase-0 ships a minimal, representative variant set:

* :class:`LobbyCommand` — a targeted lobby-orchestration command (command lane).
* :class:`ReportAck` — an acknowledgement of a result-report / command.

The backend<->desktop-client lobby-orchestration plane (PLAN.md §3.6) adds the
explicit, strongly-typed command/ack envelopes the autonomous LCU match operation
needs:

backend -> client (command lane, §3.6.1 write allow-list):

* :class:`CreateLobbyCommand` — re-creatable immutable lobby spec (§3.6.2).
* :class:`InvitePlayersCommand` — batched invite list (§3.6.3).
* :class:`PromoteHostCommand` — lossless host promotion (§3.6.1).
* :class:`AutoAcceptCommand` — guest arms auto-accept of the host's invite (§3.6.3).
* :class:`AbortCommand` — tear-down / champ-select abort (§3.6.1).

client -> server (ack / liveness / result lane):

* :class:`LobbyAck` — at-most-once ack of a command, with the live ``lcu_lobby_id``.
* :class:`HostHeartbeat` — pre-game host liveness (§3.6.1 watchdog input).
* :class:`ResultReportEnvelope` — end-of-game report carrier (§3.6.5).

Every envelope keeps ``origin_guild_id`` + an :class:`AudienceDescriptor` +
``command_id`` (at-most-once) and ``host_epoch`` wherever the host-lease fence
applies (§3.6.1).
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = "0"


class _Strict(BaseModel):
    """Base config: forbid unknown fields so wire drift is caught early."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _Wire(_Strict):
    """Base for the models that actually cross the socket to the desktop client.

    **The WS plane speaks camelCase.** It always did in the client->server
    direction — the hub reads ``matchId`` off the subscribe frame and ``hostEpoch``
    / ``lcuLobbyId`` off acks and heartbeats — but the server->client direction
    serialized these models by Python field name and so shipped snake_case. The
    desktop reads ``command.matchId``, got ``undefined``, and dropped **every**
    command on its first line without an ack or a log. Aliasing here is what makes
    both directions the same contract.

    ``populate_by_name`` keeps Python-side construction (``CreateLobbyCommand(
    match_id=...)``) working, so this is a wire-format change only — no caller
    moves to camelCase keyword arguments. ``serialize_by_alias`` makes a plain
    ``model_dump_json()`` emit the aliases, including when the model is nested
    inside an :class:`Envelope` (which deliberately stays snake_case: it never
    reaches the client, and the hub unwraps it by ``origin_guild_id``/``payload``).

    ``type`` is untouched by the generator (one word), so the discriminated union
    keeps working exactly as before.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AudienceKind(StrEnum):
    """Who an envelope is targeted at."""

    GUILD = "guild"  # everyone in the origin guild
    MATCH = "match"  # all participants of a match
    CLIENT = "client"  # a single desktop client (by puuid)
    BROADCAST = "broadcast"  # platform-wide (operator use)


class AudienceDescriptor(_Strict):
    """Targeting descriptor for an :class:`Envelope`.

    ``ref`` scopes the audience (e.g. a match id for ``MATCH``, a puuid for
    ``CLIENT``). It is optional for guild/broadcast audiences.
    """

    kind: AudienceKind
    ref: str | None = Field(
        default=None,
        description="Audience scope ref (match id, puuid, ...); None for guild/broadcast.",
    )


class LobbyCommandKind(StrEnum):
    """Bounded lobby/setup command kinds (PLAN.md §3.6 write allow-list)."""

    CREATE_LOBBY = "create_lobby"
    INVITE = "invite"
    AUTO_ACCEPT = "auto_accept"
    PROMOTE = "promote"
    START_CHAMP_SELECT = "start_champ_select"
    ABORT = "abort"


class LobbyCommand(_Strict):
    """A targeted lobby-orchestration command (backend -> client).

    Carries the epoch fence + idempotency key the command lane requires
    (PLAN.md §3.6.1: ``command_id`` at-most-once, ``host_epoch`` stale-reject).
    """

    type: Literal["lobby_command"] = "lobby_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Monotonic host lease fence.")
    kind: LobbyCommandKind
    args: dict[str, Any] = Field(default_factory=dict)


class ReportAck(_Strict):
    """Acknowledgement of a command or result report (client -> backend)."""

    type: Literal["report_ack"] = "report_ack"
    command_id: str = Field(description="The command/report being acked.")
    accepted: bool = True
    detail: str | None = None


# ---------------------------------------------------------------------------
# Lobby orchestration plane (PLAN.md §3.6) — backend <-> desktop-client.
# ---------------------------------------------------------------------------


class LobbyPhase(StrEnum):
    """Pre-game / in-game lobby phases (PLAN.md §3.6.1 state machine).

    Mirrors ``MatchLobby.state`` so a heartbeat phase round-trips against the
    persisted lobby state.
    """

    ASSIGNED_HOST = "assigned_host"
    AWAITING_CREATE = "awaiting_create"
    LOBBY_OPEN = "lobby_open"
    FILLING = "filling"
    READY = "ready"
    STARTING = "starting"
    IN_GAME = "in_game"
    RESULT_PENDING = "result_pending"
    ENDED = "ended"
    ABORTED = "aborted"
    HOST_LOST = "host_lost"


class ResultSource(StrEnum):
    """Provenance of a result report (PLAN.md §3.6.5 / §3.6.6)."""

    EOG_STATS_BLOCK = "eog_stats_block"
    LIVE_CLIENT_DATA = "live_client_data"


class LobbySpec(_Wire):
    """The immutable, re-creatable lobby spec (PLAN.md §3.6.2).

    Backend-owned so any successor host re-creates the lobby **verbatim**.
    Defaults mirror the canonical 5v5 config ``{gameMode:"CLASSIC", mapId:11,
    teamSize:5}`` (``teamSize`` caps each side, **not** the invitee list); a 1v1
    duel overrides these to ``{gameMode:"ARAM", mapId:12, teamSize:1}``.
    """

    game_mode: str = Field(default="CLASSIC", description="LCU customGameLobby gameMode.")
    map_id: int = Field(default=11, description="LCU mapId (11 = Rift, 12 = Howling Abyss).")
    team_size: Literal[1, 5] = Field(default=5, description="Per-team cap (5 = 5v5, 1 = 1v1).")
    lobby_name: str = Field(description="Stable lobby name (embeds match_id for audit).")
    lobby_password: str = Field(description="Stable lobby password for manual fallback.")
    invitee_summoner_ids: list[str] = Field(
        default_factory=list,
        description="Exact invitee summonerId list (PLAN.md §3.6.2/§3.6.3).",
    )
    invitee_puuids: list[str] = Field(
        default_factory=list,
        description="Exact invitee PUUID list (collision-immune join key).",
    )


class CreateLobbyCommand(_Wire):
    """Backend -> host: create (or re-create) the custom lobby (PLAN.md §3.6.2).

    Fenced by ``(match_id, host_epoch)`` so a stale-epoch successor is rejected;
    ``host_epoch`` is the monotonic host lease. ``command_id`` is the at-most-once
    idempotency key (the host returns the existing lobby handle for a replay).
    """

    type: Literal["create_lobby_command"] = "create_lobby_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Monotonic host lease fence.")
    spec: LobbySpec = Field(description="Immutable, verbatim-re-creatable lobby spec.")


class InvitePlayersCommand(_Wire):
    """Backend -> host: batched invite of the full roster (PLAN.md §3.6.3).

    Invites are keyed by ``(match_id, toSummonerId, host_epoch)`` on the host.
    """

    type: Literal["invite_players_command"] = "invite_players_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Monotonic host lease fence.")
    to_summoner_ids: list[str] = Field(
        default_factory=list,
        description="Full toSummonerId array for one batched invitations POST.",
    )
    to_puuids: list[str] = Field(
        default_factory=list,
        description="Corresponding PUUIDs (auto-accept inviter-match guard).",
    )


class PromoteHostCommand(_Wire):
    """Backend -> client: promote a designated successor to host (PLAN.md §3.6.1).

    The lossless failover path: ``new_host_epoch`` is the freshly-leased epoch;
    ``new_host_puuid`` is the next opted-in eligible successor.
    """

    type: Literal["promote_host_command"] = "promote_host_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Epoch being superseded (fence).")
    new_host_epoch: int = Field(ge=0, description="Freshly-leased successor epoch.")
    new_host_puuid: str = Field(description="Designated successor host PUUID.")


class AutoAcceptCommand(_Wire):
    """Backend -> guest: arm auto-accept for the host's invite (PLAN.md §3.6.3).

    The other half of the invite: the host's client sends nine invites, and
    without this nine people still have to click Accept by hand — which is most of
    the manual work the autonomous path exists to remove. The kind was declared in
    :class:`LobbyCommandKind` from the start but no command model ever carried it,
    so it could not be sent at all.

    ``expected_inviter_puuid`` is the fence: a client auto-accepts an invite from
    the **designated host for this match** and nothing else, so arming it can never
    be turned into "accept whatever anyone sends you". Paired with ``host_epoch``,
    an invite from a deposed host is refused too.

    The canonical 78-char PUUID alone cannot close the fence on the client: the
    LCU's ``received-invitations`` record identifies the inviter by the LCU-local
    36-char ``fromPuuid`` and the per-region numeric ``fromSummonerId`` — it never
    carries the canonical PUUID, so comparing against it matches nothing and every
    invite sat un-accepted. ``expected_inviter_lcu_puuid`` /
    ``expected_inviter_summoner_id`` are the host's advisory identifiers captured
    at desktop verification, i.e. the identifiers the guest's client can actually
    observe; matching ANY of the three accepts. Optional because verification may
    predate their capture — the canonical fence still travels for that case.
    """

    type: Literal["auto_accept_command"] = "auto_accept_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Monotonic host lease fence.")
    expected_inviter_puuid: str = Field(
        description="Only auto-accept invites from this designated-host PUUID."
    )
    expected_inviter_lcu_puuid: str | None = Field(
        default=None,
        description="Host's advisory LCU-local 36-char PUUID (matches fromPuuid).",
    )
    expected_inviter_summoner_id: str | None = Field(
        default=None,
        description="Host's per-region numeric summonerId (matches fromSummonerId).",
    )


class AbortCommand(_Wire):
    """Backend -> client: abort champ-select / tear down the lobby (PLAN.md §3.6.1)."""

    type: Literal["abort_command"] = "abort_command"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    host_epoch: int = Field(ge=0, description="Monotonic host lease fence.")
    reason: str | None = Field(default=None, description="Audited abort reason.")


class LobbyAck(_Wire):
    """Client -> backend: ack of a lobby command (PLAN.md §3.6.1/§3.6.2).

    ``ok`` is false when the client refused (e.g. stale epoch); the host reports
    the live ``lcu_lobby_id`` back on a successful (re-)create so the backend can
    re-broadcast it for the manual fallback.
    """

    type: Literal["lobby_ack"] = "lobby_ack"
    command_id: str = Field(description="The command being acked (at-most-once).")
    host_epoch: int = Field(ge=0, description="Epoch the client acted under.")
    ok: bool = Field(description="False ⇒ refused (e.g. stale epoch).")
    lcu_lobby_id: str | None = Field(
        default=None,
        description="Live LCU lobby handle on a successful (re-)create.",
    )
    detail: str | None = None


class HostHeartbeat(_Wire):
    """Client -> backend: pre-game host liveness (PLAN.md §3.6.1 watchdog input).

    Posted every ~2-3s while pre-game. The per-match arq watchdog triggers
    failover after a bounded grace window of missed heartbeats **AND** only if
    ``phase`` has not legitimately advanced.
    """

    type: Literal["host_heartbeat"] = "host_heartbeat"
    match_id: str
    host_epoch: int = Field(ge=0, description="Epoch this host owns.")
    phase: LobbyPhase = Field(description="Current gameflow-derived lobby phase.")
    members_present: int = Field(ge=0, description="Members currently in the lobby.")
    last_seen: str = Field(description="Client-local at-keyboard liveness timestamp (ISO-8601).")
    lcu_lobby_id: str | None = Field(default=None, description="Live LCU lobby handle, if open.")


class ResultReportEnvelope(_Wire):
    """Client -> backend: end-of-game result report carrier (PLAN.md §3.6.5).

    Keyed by ``(lcu_game_id, reporter_puuid, source)``; carries the server-issued
    ``nonce`` + ``schema_version`` for the freshness/auth layer (§3.6.6).
    """

    type: Literal["result_report"] = "result_report"
    command_id: str = Field(description="At-most-once idempotency key.")
    match_id: str
    lcu_game_id: str = Field(description="Platform-established gameId.")
    reporter_puuid: str = Field(description="Linked-PUUID reporter.")
    source: ResultSource = Field(description="eog_stats_block | live_client_data.")
    claimed_winner_team: str | None = Field(default=None, description="Reporter's winner bit.")
    reported_kda: dict[str, Any] = Field(default_factory=dict, description="Per-player KDA.")
    kill_stream: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Captured kill-stream corroboration signal.",
    )
    nonce: str = Field(description="Server-issued single-use, short-TTL nonce.")
    report_schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Report payload schema version (§3.6.5).",
    )


EnvelopePayload = Annotated[
    LobbyCommand
    | ReportAck
    | CreateLobbyCommand
    | InvitePlayersCommand
    | PromoteHostCommand
    | AutoAcceptCommand
    | AbortCommand
    | LobbyAck
    | HostHeartbeat
    | ResultReportEnvelope,
    Field(discriminator="type"),
]


class Envelope(_Strict):
    """The universal WS envelope.

    ``origin_guild_id`` + ``audience`` are present on **every** message
    (PLAN.md §5); ``payload`` is the discriminated command/ack body.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)
    origin_guild_id: str = Field(description="Tenant of origin (RLS authz).")
    audience: AudienceDescriptor
    payload: EnvelopePayload


def export_json_schema() -> dict[str, Any]:
    """Return the canonical JSON schema for :class:`Envelope`.

    Consumed by ``make gen-clients`` and CI drift-checks. Covers the full
    payload union including the lobby-orchestration command/ack types
    (PLAN.md §3.6).
    """
    return Envelope.model_json_schema()


def _cli() -> None:
    """Console entry point: print the envelope JSON schema to stdout."""
    print(json.dumps(export_json_schema(), indent=2, sort_keys=True))


__all__ = [
    "SCHEMA_VERSION",
    "AbortCommand",
    "AutoAcceptCommand",
    "AudienceDescriptor",
    "AudienceKind",
    "CreateLobbyCommand",
    "Envelope",
    "EnvelopePayload",
    "HostHeartbeat",
    "InvitePlayersCommand",
    "LobbyAck",
    "LobbyCommand",
    "LobbyCommandKind",
    "LobbyPhase",
    "LobbySpec",
    "PromoteHostCommand",
    "ReportAck",
    "ResultReportEnvelope",
    "ResultSource",
    "export_json_schema",
]
