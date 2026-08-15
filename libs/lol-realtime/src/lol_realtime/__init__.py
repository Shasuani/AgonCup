"""lol-realtime: WebSocket envelope models + exported JSON schema.

The one real-time wire contract (PLAN.md §1.4 / §5). Models are the source of
truth; the JSON schema is exported for gen-clients and CI drift-checks.
"""

from __future__ import annotations

from lol_realtime.envelopes import (
    AbortCommand,
    AudienceDescriptor,
    AudienceKind,
    AutoAcceptCommand,
    CreateLobbyCommand,
    Envelope,
    EnvelopePayload,
    HostHeartbeat,
    InvitePlayersCommand,
    LobbyAck,
    LobbyCommand,
    LobbyCommandKind,
    LobbyPhase,
    LobbySpec,
    PromoteHostCommand,
    ReportAck,
    ResultReportEnvelope,
    ResultSource,
    export_json_schema,
)

__all__ = [
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

__version__ = "0.0.0"
