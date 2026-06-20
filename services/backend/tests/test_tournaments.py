"""Tests for the tournament bracket planner (PLAN.md §3.1).

``plan_bracket_matches`` is pure (no DB), so the bracket->Match mapping is tested
exhaustively here. The async CRUD/lifecycle in the service is covered by the
Postgres integration suite (RLS-dependent) once a DB is wired in CI.
"""

from __future__ import annotations

import uuid

import pytest
from lol_schemas.enums import MatchStatus

from app.tournaments.service import plan_bracket_matches

# (num_teams, bracket_size, expected_byes)
_CASES = [
    (2, 2, 0),
    (3, 4, 1),
    (5, 8, 3),
    (8, 8, 0),
    (11, 16, 5),
    (16, 16, 0),
]


def _teams(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


@pytest.mark.parametrize("n, bracket_size, expected_byes", _CASES)
def test_match_and_bye_counts(n: int, bracket_size: int, expected_byes: int) -> None:
    tid, gid = uuid.uuid4(), uuid.uuid4()
    teams = _teams(n)
    matches = plan_bracket_matches(tournament_id=tid, guild_id=gid, seeded_team_ids=teams)

    # Single elimination over a power-of-two bracket has bracket_size - 1 nodes.
    assert len(matches) == bracket_size - 1

    byes = [m for m in matches if m.status == MatchStatus.WALKOVER]
    assert len(byes) == expected_byes

    # Every row carries the tenant + tournament.
    assert all(m.guild_id == gid and m.tournament_id == tid for m in matches)


@pytest.mark.parametrize("n, bracket_size, expected_byes", _CASES)
def test_bye_matches_preadvance_winner(n: int, bracket_size: int, expected_byes: int) -> None:
    matches = plan_bracket_matches(
        tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=_teams(n)
    )
    for m in matches:
        if m.status == MatchStatus.WALKOVER:
            # Bye: a present team auto-advances, no opponent.
            assert m.team_a is not None
            assert m.team_b is None
            assert m.winner_team_id == m.team_a
        else:
            assert m.status == MatchStatus.PENDING
            assert m.winner_team_id is None


def test_round_one_real_matches_have_both_teams() -> None:
    matches = plan_bracket_matches(
        tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=_teams(8)
    )
    round_one = [m for m in matches if m.round == 1]
    assert len(round_one) == 4
    assert all(m.team_a is not None and m.team_b is not None for m in round_one)


def test_later_rounds_are_empty_placeholders() -> None:
    matches = plan_bracket_matches(
        tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=_teams(8)
    )
    later = [m for m in matches if m.round > 1]
    # 8-team bracket: round 2 (semis, 2) + round 3 (final, 1) = 3 placeholders.
    assert len(later) == 3
    assert all(m.team_a is None and m.team_b is None for m in later)
    assert max(m.round for m in matches) == 3  # log2(8)


def test_seeds_must_be_distinct_and_in_range() -> None:
    with pytest.raises(ValueError):
        plan_bracket_matches(
            tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=_teams(17)
        )
    one = _teams(1)
    with pytest.raises(ValueError):
        plan_bracket_matches(tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=one)
    dup = _teams(1) * 2
    with pytest.raises(ValueError):
        plan_bracket_matches(tournament_id=uuid.uuid4(), guild_id=uuid.uuid4(), seeded_team_ids=dup)
