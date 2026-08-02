"""Smoke tests: every representative DTO imports and round-trips through pydantic."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

import lol_schemas as ls


def _now() -> datetime:
    return datetime.now(tz=UTC)


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


def test_user_guild_role_enum_values() -> None:
    """The canonical per-(user, guild) RBAC role enum (PLAN.md §3.1 auth)."""
    assert hasattr(ls, "UserGuildRoleName")
    values = {m.value for m in ls.UserGuildRoleName}
    assert values == {"platform_operator", "guild_admin", "captain", "player"}
    # str-enum: value comparison works for wire/DB round-trips.
    assert ls.UserGuildRoleName.GUILD_ADMIN == "guild_admin"


def test_riot_account_puuid_canonical_required() -> None:
    acct = ls.RiotAccountDTO(id=uuid4(), user_id=uuid4(), puuid="PUUID-ABC")
    assert acct.puuid == "PUUID-ABC"
    assert acct.verified is False
    with pytest.raises(ValidationError):
        ls.RiotAccountDTO(id=uuid4(), user_id=uuid4(), puuid="")


def test_tournament_max_teams_capped_at_16() -> None:
    t = ls.TournamentDTO(id=uuid4(), guild_id=uuid4(), name="Cup")
    assert t.max_teams == 16
    with pytest.raises(ValidationError):
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


def test_desktop_verify_request_new_shape() -> None:
    """Request drops canonical puuid; carries Riot ID + region + advisory signals."""
    req = ls.DesktopVerifyRequestDTO(
        code="ABCD-EFGH",
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        lcu_puuid="ec15f3f7-785e-57e8-8931-590e59d62f9e",
        summoner_id="55997829",
    )
    assert req.game_name == "Chama"
    assert req.tag_line == "Shasu"
    assert req.region == "eun1"
    assert req.lcu_puuid is not None and req.summoner_id == "55997829"

    # Optional advisory fields default to None.
    minimal = ls.DesktopVerifyRequestDTO(
        code="ABCD-EFGH", game_name="g", tag_line="t", region="na1"
    )
    assert minimal.lcu_puuid is None
    assert minimal.summoner_id is None

    # game_name/tag_line are required (canonical puuid no longer accepted).
    with pytest.raises(ValidationError):
        ls.DesktopVerifyRequestDTO(code="ABCD-EFGH", region="na1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ls.DesktopVerifyRequestDTO(
            code="ABCD-EFGH",
            game_name="g",
            tag_line="t",
            region="na1",
            puuid="legacy",  # type: ignore[call-arg]
        )


def test_desktop_verify_response_rank_snapshot() -> None:
    resp = ls.DesktopVerifyResponseDTO(
        account_id=uuid4(),
        puuid="canonical-78-char-puuid",
        verified=True,
        game_name="Chama",
        tag_line="Shasu",
        region="eun1",
        tier="GOLD",
        division="II",
        league_points=45,
        rank_text="Gold II, 45 LP",
    )
    assert resp.verified is True
    assert resp.tier == "GOLD"
    assert resp.rank_text == "Gold II, 45 LP"

    # rank_text defaults to "Unranked"; rank fields default to None.
    unranked = ls.DesktopVerifyResponseDTO(account_id=uuid4(), puuid="p", verified=True)
    assert unranked.rank_text == "Unranked"
    assert unranked.tier is None
    assert unranked.league_points is None


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ls.UserDTO(
            id=uuid4(),
            discord_user_id="123",
            display_name="bob",
            created_at=_now(),
            unexpected="nope",
        )
