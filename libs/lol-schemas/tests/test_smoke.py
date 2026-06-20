"""Smoke tests: every representative DTO imports and round-trips through pydantic."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import lol_schemas as ls


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_package_exports_present() -> None:
    for name in (
        "GuildDTO",
        "UserDTO",
        "RiotAccountDTO",
        "TeamDTO",
        "TournamentDTO",
        "MatchDTO",
        "MatchLobbyDTO",
        "ResultReportDTO",
        "MatchResultDTO",
        "LobbyPhase",
    ):
        assert hasattr(ls, name), f"missing export: {name}"
    assert ls.MAX_TEAMS == 16


def test_lobby_phase_enum_members() -> None:
    expected = {
        "ASSIGNED_HOST",
        "AWAITING_CREATE",
        "LOBBY_OPEN",
        "FILLING",
        "READY",
        "STARTING",
        "IN_GAME",
        "RESULT_PENDING",
    }
    members = {m.name for m in ls.LobbyPhase}
    assert expected.issubset(members)


def test_riot_account_puuid_canonical_required() -> None:
    acct = ls.RiotAccountDTO(id=uuid4(), user_id=uuid4(), puuid="PUUID-ABC")
    assert acct.puuid == "PUUID-ABC"
    assert acct.verified is False
    with pytest.raises(Exception):
        ls.RiotAccountDTO(id=uuid4(), user_id=uuid4(), puuid="")


def test_tournament_max_teams_capped_at_16() -> None:
    t = ls.TournamentDTO(id=uuid4(), guild_id=uuid4(), name="Cup")
    assert t.max_teams == 16
    with pytest.raises(Exception):
        ls.TournamentDTO(id=uuid4(), guild_id=uuid4(), name="Too big", max_teams=17)


def test_match_lobby_default_phase_and_roundtrip() -> None:
    lobby = ls.MatchLobbyDTO(
        id=uuid4(),
        guild_id=uuid4(),
        match_id=uuid4(),
        lobby_name="LOL-match-1",
        host_succession=["p1", "p2", "p3"],
        host_epoch=1,
    )
    assert lobby.phase is ls.LobbyPhase.ASSIGNED_HOST
    dumped = lobby.model_dump_json()
    again = ls.MatchLobbyDTO.model_validate_json(dumped)
    assert again == lobby


def test_result_report_and_match_result_roundtrip() -> None:
    team_a, team_b = uuid4(), uuid4()
    report = ls.ResultReportDTO(
        guild_id=uuid4(),
        match_id=uuid4(),
        lcu_game_id="GAME-123",
        reporter_puuid="puuid-1",
        source=ls.ResultSource.EOG_STATS_BLOCK,
        winner=team_a,
        per_player_kda=[ls.PlayerKDA(puuid="puuid-1", kills=5, deaths=2, assists=7)],
    )
    assert report.winner == team_a
    assert report.per_player_kda[0].kills == 5

    result = ls.MatchResultDTO(
        guild_id=uuid4(),
        match_id=uuid4(),
        tournament_id=uuid4(),
        agreed_game_id="GAME-123",
        winner_team_id=team_b,
        quorum_detail=ls.QuorumDetail(reports_counted=6, both_teams_represented=True),
        decided_at=_now(),
    )
    assert result.state is ls.MatchResultState.PROVISIONAL
    assert result.quorum_detail.both_teams_represented is True


def test_extra_fields_forbidden() -> None:
    with pytest.raises(Exception):
        ls.UserDTO(
            id=uuid4(),
            discord_user_id="123",
            display_name="bob",
            created_at=_now(),
            unexpected="nope",
        )
