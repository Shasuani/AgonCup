"""Tests for envelope models + JSON-schema export."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lol_realtime import (
    AbortCommand,
    AudienceDescriptor,
    AudienceKind,
    CreateLobbyCommand,
    Envelope,
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


def test_export_json_schema_works() -> None:
    schema = export_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("title") == "Envelope"
    props = schema["properties"]
    # The load-bearing universal fields are present on every envelope.
    assert "origin_guild_id" in props
    assert "audience" in props
    assert "payload" in props


def test_export_json_schema_covers_lobby_orchestration_types() -> None:
    """Every new §3.6 payload type must surface in the exported schema (gen-clients)."""
    schema = export_json_schema()
    defs = schema["$defs"]
    for name in (
        "CreateLobbyCommand",
        "InvitePlayersCommand",
        "PromoteHostCommand",
        "AbortCommand",
        "LobbyAck",
        "HostHeartbeat",
        "ResultReportEnvelope",
        "LobbySpec",
    ):
        assert name in defs, f"{name} missing from exported schema $defs"


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


# ---------------------------------------------------------------------------
# Lobby orchestration plane (PLAN.md §3.6).
# ---------------------------------------------------------------------------


def _wrap(payload: object) -> Envelope:
    return Envelope(
        origin_guild_id="g1",
        audience=AudienceDescriptor(kind=AudienceKind.MATCH, ref="m42"),
        payload=payload,  # type: ignore[arg-type]
    )


def test_create_lobby_command_roundtrips_with_spec() -> None:
    env = _wrap(
        CreateLobbyCommand(
            command_id="c1",
            match_id="m42",
            host_epoch=2,
            spec=LobbySpec(
                lobby_name="LoL-m42",
                lobby_password="pw",
                invitee_summoner_ids=["s1", "s2"],
                invitee_puuids=["p1", "p2"],
            ),
        )
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, CreateLobbyCommand)
    assert back.payload.spec.game_mode == "CLASSIC"
    assert back.payload.spec.map_id == 11
    assert back.payload.spec.team_size == 5
    assert back.payload.spec.invitee_puuids == ["p1", "p2"]
    # Universal envelope invariants present on the orchestration plane too.
    assert back.origin_guild_id == "g1"
    assert back.audience.kind is AudienceKind.MATCH


def test_lobby_spec_team_size_is_fixed_at_five() -> None:
    """teamSize=5 caps the lobby at 10, not the invitee list (PLAN.md §3.6.2)."""
    with pytest.raises(ValidationError):
        LobbySpec(lobby_name="n", lobby_password="p", team_size=3)  # type: ignore[arg-type]


def test_invite_players_command_roundtrips() -> None:
    env = _wrap(
        InvitePlayersCommand(
            command_id="c2",
            match_id="m42",
            host_epoch=2,
            to_summoner_ids=["s1", "s2"],
            to_puuids=["p1", "p2"],
        )
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, InvitePlayersCommand)
    assert back.payload.to_summoner_ids == ["s1", "s2"]


def test_promote_host_command_roundtrips() -> None:
    env = _wrap(
        PromoteHostCommand(
            command_id="c3",
            match_id="m42",
            host_epoch=2,
            new_host_epoch=3,
            new_host_puuid="p9",
        )
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, PromoteHostCommand)
    assert back.payload.new_host_epoch == 3
    assert back.payload.new_host_puuid == "p9"


def test_abort_command_roundtrips() -> None:
    env = _wrap(
        AbortCommand(command_id="c4", match_id="m42", host_epoch=2, reason="champ_select_abort")
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, AbortCommand)
    assert back.payload.reason == "champ_select_abort"


def test_lobby_ack_roundtrips_with_lcu_lobby_id() -> None:
    env = _wrap(LobbyAck(command_id="c1", host_epoch=2, ok=True, lcu_lobby_id="lcu-123"))
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, LobbyAck)
    assert back.payload.ok is True
    assert back.payload.lcu_lobby_id == "lcu-123"


def test_host_heartbeat_roundtrips() -> None:
    env = _wrap(
        HostHeartbeat(
            match_id="m42",
            host_epoch=2,
            phase=LobbyPhase.FILLING,
            members_present=7,
            last_seen="2026-06-20T12:00:00Z",
            lcu_lobby_id="lcu-123",
        )
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, HostHeartbeat)
    assert back.payload.phase is LobbyPhase.FILLING
    assert back.payload.members_present == 7


def test_result_report_envelope_roundtrips() -> None:
    env = _wrap(
        ResultReportEnvelope(
            command_id="c5",
            match_id="m42",
            lcu_game_id="g-999",
            reporter_puuid="p1",
            source=ResultSource.EOG_STATS_BLOCK,
            claimed_winner_team="team_a",
            reported_kda={"p1": {"k": 5, "d": 2, "a": 9}},
            kill_stream=[{"KillerName": "p1", "VictimName": "p6", "EventTime": 12.3}],
            nonce="nonce-abc",
        )
    )
    back = Envelope.model_validate_json(env.model_dump_json())
    assert isinstance(back.payload, ResultReportEnvelope)
    assert back.payload.source is ResultSource.EOG_STATS_BLOCK
    assert back.payload.claimed_winner_team == "team_a"
    assert back.payload.kill_stream[0]["KillerName"] == "p1"


def test_stale_epoch_is_representable_on_command_and_ack() -> None:
    """A command at a superseded epoch + a refusing ack are both expressible (§3.6.1)."""
    # Backend issues a CREATE at epoch 1; current lease is already 2 (stale).
    stale_cmd = _wrap(
        CreateLobbyCommand(
            command_id="c1",
            match_id="m42",
            host_epoch=1,
            spec=LobbySpec(lobby_name="LoL-m42", lobby_password="pw"),
        )
    )
    cmd_back = Envelope.model_validate_json(stale_cmd.model_dump_json())
    assert isinstance(cmd_back.payload, CreateLobbyCommand)
    assert cmd_back.payload.host_epoch == 1

    # Client refuses the stale-epoch command: ok=False carries the epoch it acted under.
    refusal = _wrap(LobbyAck(command_id="c1", host_epoch=1, ok=False, detail="stale_epoch"))
    ack_back = Envelope.model_validate_json(refusal.model_dump_json())
    assert isinstance(ack_back.payload, LobbyAck)
    assert ack_back.payload.ok is False
    assert ack_back.payload.detail == "stale_epoch"

    # A heartbeat reporting host_lost at a superseded epoch is also representable.
    hb = _wrap(
        HostHeartbeat(
            match_id="m42",
            host_epoch=1,
            phase=LobbyPhase.HOST_LOST,
            members_present=0,
            last_seen="2026-06-20T12:00:00Z",
        )
    )
    hb_back = Envelope.model_validate_json(hb.model_dump_json())
    assert isinstance(hb_back.payload, HostHeartbeat)
    assert hb_back.payload.phase is LobbyPhase.HOST_LOST


def test_host_epoch_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        CreateLobbyCommand(
            command_id="c1",
            match_id="m42",
            host_epoch=-1,
            spec=LobbySpec(lobby_name="n", lobby_password="p"),
        )
