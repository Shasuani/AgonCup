"""Tests for envelope models + JSON-schema export."""

from __future__ import annotations

from lol_realtime import (
    AudienceDescriptor,
    AudienceKind,
    Envelope,
    LobbyCommand,
    LobbyCommandKind,
    ReportAck,
    export_json_schema,
)


def test_export_json_schema_works() -> None:
    schema = export_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("title") == "Envelope"
    props = schema["properties"]
    # The load-bearing universal fields are present on every envelope.
    assert "origin_guild_id" in props
    assert "audience" in props
    assert "payload" in props


def test_command_envelope_roundtrips() -> None:
    env = Envelope(
        origin_guild_id="g1",
        audience=AudienceDescriptor(kind=AudienceKind.MATCH, ref="m42"),
        payload=LobbyCommand(
            command_id="c1",
            match_id="m42",
            host_epoch=3,
            kind=LobbyCommandKind.CREATE_LOBBY,
        ),
    )
    dumped = env.model_dump_json()
    back = Envelope.model_validate_json(dumped)
    assert isinstance(back.payload, LobbyCommand)
    assert back.payload.host_epoch == 3
    assert back.payload.kind is LobbyCommandKind.CREATE_LOBBY


def test_ack_discriminates_on_type() -> None:
    env = Envelope.model_validate(
        {
            "origin_guild_id": "g1",
            "audience": {"kind": "client", "ref": "puuid-abc"},
            "payload": {"type": "report_ack", "command_id": "c1", "accepted": True},
        }
    )
    assert isinstance(env.payload, ReportAck)
    assert env.payload.accepted is True
