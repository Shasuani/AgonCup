"""The backend half of the backend<->desktop wire contract (PLAN.md §3.6.1).

Pins the **exact bytes** every orchestration command serializes to, against a
golden fixture the desktop's own test reads. One file, two languages: that is the
whole point.

Without it, both sides were green and neither could talk to the other. The
backend published ``{"type": "create_lobby_command", "match_id": ...}``; the
desktop discriminated on ``kind`` and read ``matchId``. The controller's first
line compared ``undefined`` against its match id and returned — every command
dropped, with no ack and no log — while each side's tests went on passing,
because each built its fixtures in its own shape.

If a change here fails, the client will stop understanding the backend. Update
the golden deliberately and update the desktop with it; never regenerate it just
to make this pass.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from lol_realtime import PromoteHostCommand

from app.orchestration.service import (
    build_abort_command,
    build_auto_accept_command,
    build_create_lobby_command,
    build_invite_command,
    build_lobby_spec,
)

GOLDEN = (
    Path(__file__).resolve().parents[3] / "libs/lol-realtime/contract/lobby-commands.golden.json"
)

_MATCH_ID = "11111111-2222-3333-4444-555555555555"


def _commands() -> dict[str, object]:
    """Every backend->client command, with fixed ids so the bytes are stable."""
    match_id = uuid.UUID(_MATCH_ID)
    spec = build_lobby_spec(
        match_id=match_id,
        lobby_name="LoL-11111111",
        lobby_password="111111",
        invitee_summoner_ids=["sid-1", "sid-2"],
        invitee_puuids=["puuid-1", "puuid-2"],
    )
    return {
        "create_lobby_command": build_create_lobby_command(
            match_id=match_id, host_epoch=0, spec=spec, command_id="cmd-create"
        ),
        "invite_players_command": build_invite_command(
            match_id=match_id,
            host_epoch=0,
            to_summoner_ids=["sid-1", "sid-2"],
            to_puuids=["puuid-1", "puuid-2"],
            command_id="cmd-invite",
        ),
        "auto_accept_command": build_auto_accept_command(
            match_id=match_id,
            host_epoch=0,
            expected_inviter_puuid="puuid-host",
            command_id="cmd-accept",
        ),
        "promote_host_command": PromoteHostCommand(
            command_id="cmd-promote",
            match_id=_MATCH_ID,
            host_epoch=0,
            new_host_epoch=1,
            new_host_puuid="puuid-2",
        ),
        "abort_command": build_abort_command(
            match_id=match_id, host_epoch=0, reason="cancelled", command_id="cmd-abort"
        ),
    }


def test_commands_serialize_to_the_golden_wire_bytes() -> None:
    golden = json.loads(GOLDEN.read_text())
    actual = {k: json.loads(v.model_dump_json()) for k, v in _commands().items()}  # type: ignore[attr-defined]
    assert actual == golden


def test_the_wire_is_camel_case_because_that_is_what_the_client_reads() -> None:
    """A named guard for the specific drift, so the failure explains itself.

    ``model_dump_json()`` defaults to field names; these models only emit aliases
    because ``_Wire`` sets ``serialize_by_alias``. Drop that and the golden test
    above fails with a large diff — this one fails saying exactly what broke.
    """
    payload = json.loads(_commands()["create_lobby_command"].model_dump_json())  # type: ignore[attr-defined]
    assert payload["matchId"] == _MATCH_ID
    assert payload["hostEpoch"] == 0
    assert payload["spec"]["inviteeSummonerIds"] == ["sid-1", "sid-2"]
    for snake in ("match_id", "host_epoch", "command_id"):
        assert snake not in payload
    assert "invitee_summoner_ids" not in payload["spec"]


def test_the_discriminator_stays_type_not_kind() -> None:
    """The union discriminates on ``type``; the client switches on the same field.

    It used to switch on ``kind`` with short names the backend never sent, so no
    case ever matched and commands fell out of the switch in silence.
    """
    assert [json.loads(c.model_dump_json())["type"] for c in _commands().values()] == [  # type: ignore[attr-defined]
        "create_lobby_command",
        "invite_players_command",
        "auto_accept_command",
        "promote_host_command",
        "abort_command",
    ]
