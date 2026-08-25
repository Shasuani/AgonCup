"""Tests for the notification + central-feed embed builders (PLAN.md §3.2)."""

from __future__ import annotations

import pytest

from lol_discord import (
    EmbedColor,
    build_central_feed_item,
    lobby_credentials,
    match_ready,
    match_result,
    registration_code,
    registration_verified,
    team_created,
    team_joined,
    tournament_check_in,
    tournament_created,
    tournament_open,
    tournament_started,
)


def _field_values(embed: dict[str, object]) -> str:
    """Concatenate every text-bearing part of an embed for substring assertions."""
    parts = [str(embed.get("title", "")), str(embed.get("description", ""))]
    for f in embed.get("fields", []):  # type: ignore[union-attr]
        parts.append(f["name"])
        parts.append(f["value"])
    footer = embed.get("footer")
    if isinstance(footer, dict):
        parts.append(str(footer.get("text", "")))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Registration                                                                 #
# --------------------------------------------------------------------------- #
def test_registration_code() -> None:
    embed = registration_code(code="7K3M-QP9R")
    assert embed["title"] == "Paste this code in the LeagueOfLeagues desktop app to sign in"
    assert "sign in" in embed["description"].lower()
    assert embed["color"] == int(EmbedColor.WARNING)
    blob = _field_values(embed)
    assert "7K3M-QP9R" in blob
    assert "desktop app" in blob.lower()
    assert "sign in" in blob.lower()
    assert "15 minutes" in blob
    # No icon/thumbnail, no Riot ID leakage.
    assert "thumbnail" not in embed


def test_registration_code_custom_expiry() -> None:
    embed = registration_code(code="ABCD-EFGH", expires_minutes=5)
    assert "5 minutes" in _field_values(embed)


def test_registration_verified() -> None:
    embed = registration_verified(riot_id="Faker#KR1")
    assert embed["title"] == "Account verified"
    assert embed["color"] == int(EmbedColor.SUCCESS)
    assert "Faker#KR1" in embed["description"]
    # Backward-compatible: no region/rank => no fields.
    assert "fields" not in embed


def test_registration_verified_with_region_and_rank() -> None:
    embed = registration_verified(
        riot_id="Chama#Shasu",
        region="EUNE",
        rank_text="Gold II, 45 LP",
    )
    assert embed["color"] == int(EmbedColor.SUCCESS)
    blob = _field_values(embed)
    assert "Chama#Shasu" in blob
    assert "EUNE" in blob
    assert "Gold II, 45 LP" in blob
    assert "Solo/Duo" in blob


def test_registration_verified_unranked() -> None:
    embed = registration_verified(
        riot_id="Chama#Shasu",
        region="EUNE",
        rank_text="Unranked",
    )
    blob = _field_values(embed)
    assert "Unranked" in blob
    assert "EUNE" in blob


# --------------------------------------------------------------------------- #
# Teams                                                                        #
# --------------------------------------------------------------------------- #
def test_team_created() -> None:
    embed = team_created(team_name="T1", captain_name="Zeus")
    assert embed["title"] == "Team created"
    blob = _field_values(embed)
    assert "T1" in blob
    assert "Zeus" in blob


def test_team_joined() -> None:
    embed = team_joined(team_name="T1", member_name="Oner", member_count=3)
    blob = _field_values(embed)
    assert "Oner" in blob
    assert "T1" in blob
    assert "3" in blob


# --------------------------------------------------------------------------- #
# Tournament lifecycle                                                         #
# --------------------------------------------------------------------------- #
def test_tournament_created() -> None:
    embed = tournament_created(name="Summer Cup", fmt="Single elimination")
    assert embed["title"] == "Tournament created"
    blob = _field_values(embed)
    assert "Summer Cup" in blob
    assert "Single elimination" in blob


def test_tournament_created_without_format() -> None:
    """``fmt`` omitted → no Format field: every tournament is a knockout bracket
    (owner decision), so a constant "single_elimination" label is pure noise."""
    embed = tournament_created(name="Summer Cup", mode="aram_1v1", best_of=1)
    field_names = [f["name"] for f in embed.get("fields", [])]
    assert "Format" not in field_names
    assert "Mode" in field_names


def test_tournament_open() -> None:
    embed = tournament_open(name="Summer Cup", slots="3 / 16")
    assert embed["color"] == int(EmbedColor.SUCCESS)
    assert "3 / 16" in _field_values(embed)


def test_tournament_check_in() -> None:
    embed = tournament_check_in(name="Summer Cup", deadline="in 30m")
    assert embed["title"] == "Check-in open"
    assert embed["color"] == int(EmbedColor.WARNING)
    blob = _field_values(embed)
    assert "in 30m" in blob
    assert "/checkin" in blob


def test_tournament_started_with_and_without_bracket() -> None:
    with_url = tournament_started(name="Summer Cup", bracket_url="https://x/y")
    assert "https://x/y" in _field_values(with_url)

    without_url = tournament_started(name="Summer Cup")
    assert "fields" not in without_url


# --------------------------------------------------------------------------- #
# Match orchestration                                                          #
# --------------------------------------------------------------------------- #
def test_match_ready_host() -> None:
    embed = match_ready(match_label="QF1", host_name="Zeus", is_host=True)
    assert embed["title"] == "Your lobby is forming"
    assert "host" in embed["description"].lower()
    assert "Zeus" in _field_values(embed)


def test_match_ready_guest_with_credentials() -> None:
    embed = match_ready(
        match_label="QF1",
        host_name="Zeus",
        is_host=False,
        lobby_name="LoL-QF1",
        lobby_password="secret123",
    )
    blob = _field_values(embed)
    assert "Zeus" in blob
    assert "LoL-QF1" in blob
    assert "secret123" in blob


def test_match_ready_guest_omits_credentials_when_absent() -> None:
    embed = match_ready(match_label="QF1", host_name="Zeus", is_host=False)
    blob = _field_values(embed)
    assert "Password" not in blob
    assert "Lobby name" not in blob


def test_match_result() -> None:
    embed = match_result(
        match_label="Final",
        winner_name="T1",
        loser_name="GenG",
        decided_by="quorum",
    )
    assert embed["color"] == int(EmbedColor.SUCCESS)
    blob = _field_values(embed)
    assert "T1" in blob
    assert "GenG" in blob
    assert "quorum" in blob


# --------------------------------------------------------------------------- #
# Central feed — federation privacy (PLAN.md §3.2)                              #
# --------------------------------------------------------------------------- #
def test_central_feed_basic_shape() -> None:
    embed = build_central_feed_item(
        headline="T1 advanced to the final",
        source_guild_name="LCK Hub",
        tournament_name="Summer Cup",
        projected_fields={"Round": "Semifinal", "Score": "2-1"},
    )
    assert embed["title"] == "T1 advanced to the final"
    assert embed["footer"]["text"] == "Central feed"
    blob = _field_values(embed)
    assert "LCK Hub" in blob
    assert "Summer Cup" in blob
    assert "Semifinal" in blob
    assert "2-1" in blob


@pytest.mark.parametrize(
    "key",
    ["puuid", "PUUID", "discord_id", "discord_user_id", "user_id", "summoner_id", "summoner_name"],
)
def test_central_feed_rejects_identifier_keys(key: str) -> None:
    with pytest.raises(ValueError, match="identifier field"):
        build_central_feed_item(
            headline="leak attempt",
            source_guild_name="LCK Hub",
            tournament_name="Summer Cup",
            projected_fields={key: "should-never-appear"},
        )


def test_central_feed_carries_no_identifiers_in_clean_projection() -> None:
    embed = build_central_feed_item(
        headline="Match decided",
        source_guild_name="LCK Hub",
        tournament_name="Summer Cup",
        projected_fields={"Winner": "T1"},
    )
    blob = _field_values(embed).lower()
    for forbidden in ("puuid", "discord", "summoner"):
        assert forbidden not in blob


def test_lobby_credentials_promises_an_invite_only_when_a_client_will_host() -> None:
    """The manual fallback must not tell the reader to wait for their client.

    ``automated=False`` is sent exactly when nobody opted in to hosting, so the
    automated wording would be a promise the backend already knows it cannot keep
    — the reader would sit waiting for a lobby nothing is going to create.
    """
    automated = _field_values(
        lobby_credentials(
            lobby_name="LoL-abc12345",
            lobby_password="ABC123",
            tournament_name="Summer Cup",
        )
    )
    assert "your client will create this lobby" in automated

    manual = _field_values(
        lobby_credentials(
            lobby_name="LoL-abc12345",
            lobby_password="ABC123",
            tournament_name="Summer Cup",
            automated=False,
        )
    )
    assert "you are opening this match" in manual
    assert "your client will create this lobby" not in manual
    # The create is the only manual step: the reader must not also be sent
    # chasing nine people, or they will start clicking invites the client is
    # already sending.
    assert "invites the other players for you" in manual
    # Either way the credentials themselves are what the reader acts on.
    for blob in (automated, manual):
        assert "LoL-abc12345" in blob and "ABC123" in blob
