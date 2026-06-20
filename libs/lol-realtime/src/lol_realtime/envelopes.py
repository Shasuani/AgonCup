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
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0"


class _Strict(BaseModel):
    """Base config: forbid unknown fields so wire drift is caught early."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AudienceKind(str, Enum):
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


class LobbyCommandKind(str, Enum):
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


EnvelopePayload = Annotated[
    LobbyCommand | ReportAck,
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

    Consumed by ``make gen-clients`` and CI drift-checks.
    """
    return Envelope.model_json_schema()


def _cli() -> None:
    """Console entry point: print the envelope JSON schema to stdout."""
    print(json.dumps(export_json_schema(), indent=2, sort_keys=True))


__all__ = [
    "SCHEMA_VERSION",
    "AudienceDescriptor",
    "AudienceKind",
    "Envelope",
    "EnvelopePayload",
    "LobbyCommand",
    "LobbyCommandKind",
    "ReportAck",
    "export_json_schema",
]
