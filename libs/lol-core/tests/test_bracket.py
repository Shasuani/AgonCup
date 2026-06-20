"""Unit tests for the single-elimination bracket engine (N=2,3,5,8,11,16)."""

from __future__ import annotations

import math

import pytest

from lol_core import (
    Bracket,
    BracketMatch,
    generate_single_elimination,
)


def _seeds(n: int) -> list[str]:
    return [f"seed-{i}" for i in range(1, n + 1)]


def _expected_bracket_size(n: int) -> int:
    return 1 << (n - 1).bit_length() if n > 1 else 2


# ---- parametrised structural invariants ------------------------------------ #
@pytest.mark.parametrize("n", [2, 3, 5, 8, 11, 16])
def test_bracket_size_and_round_count(n: int) -> None:
    b = generate_single_elimination(_seeds(n))
    size = _expected_bracket_size(n)
    assert b.bracket_size == size
    assert b.num_teams == n
    # rounds = log2(bracket_size)
    assert len(b.rounds) == int(math.log2(size))
    # round r has size / 2**r matches
    for idx, rnd in enumerate(b.rounds, start=1):
        assert rnd.round_no == idx
        assert len(rnd.matches) == size // (2**idx)


@pytest.mark.parametrize("n", [2, 3, 5, 8, 11, 16])
def test_total_match_count(n: int) -> None:
    b = generate_single_elimination(_seeds(n))
    size = _expected_bracket_size(n)
    # A single-elim bracket of `size` slots always has exactly size-1 matches in
    # the tree (including auto-advanced byes occupying round-1 slots).
    assert b.total_matches == size - 1


@pytest.mark.parametrize(
    "n,expected_byes",
    [(2, 0), (3, 1), (5, 3), (8, 0), (11, 5), (16, 0)],
)
def test_bye_count(n: int, expected_byes: int) -> None:
    b = generate_single_elimination(_seeds(n))
    assert b.num_byes == expected_byes
    byes = [m for m in b.rounds[0].matches if m.is_bye]
    assert len(byes) == expected_byes
    # Each bye carries exactly the present team and a pre-filled winner.
    for m in byes:
        assert m.team_b is None
        assert m.team_a is not None
        assert m.winner == m.team_a


@pytest.mark.parametrize("n", [2, 3, 5, 8, 11, 16])
def test_no_duplicate_seeds_and_all_present_in_round1(n: int) -> None:
    b = generate_single_elimination(_seeds(n))
    placed: list[str] = []
    for m in b.rounds[0].matches:
        placed.extend(m.seeds_present)  # type: ignore[arg-type]
    # Every real team appears exactly once in round 1, no duplicates.
    assert sorted(placed) == sorted(_seeds(n))
    assert len(placed) == len(set(placed)) == n


@pytest.mark.parametrize("n", [3, 5, 11])
def test_byes_go_to_top_seeds(n: int) -> None:
    """The teams receiving a bye must be the top `num_byes` seeds."""
    seeds = _seeds(n)
    b = generate_single_elimination(seeds)
    bye_teams = {m.team_a for m in b.rounds[0].matches if m.is_bye}
    top_seeds = set(seeds[: b.num_byes])
    assert bye_teams == top_seeds


def test_seed1_vs_lowest_in_round1_when_no_bye() -> None:
    # 8 teams -> full bracket, #1 seed faces #8 seed in round 1, top slot.
    seeds = _seeds(8)
    b = generate_single_elimination(seeds)
    first = b.rounds[0].matches[0]
    assert {first.team_a, first.team_b} == {"seed-1", "seed-8"}


def test_later_rounds_are_placeholders() -> None:
    b = generate_single_elimination(_seeds(8))
    for rnd in b.rounds[1:]:
        for m in rnd.matches:
            assert m.team_a is None and m.team_b is None
            assert not m.is_bye
            assert m.winner is None


def test_all_matches_helper_returns_every_match() -> None:
    b = generate_single_elimination(_seeds(11))
    flat = b.all_matches()
    assert len(flat) == b.total_matches
    assert all(isinstance(m, BracketMatch) for m in flat)


# ---- validation ------------------------------------------------------------ #
@pytest.mark.parametrize("n", [0, 1, 17, 32])
def test_team_count_out_of_range_raises(n: int) -> None:
    with pytest.raises(ValueError):
        generate_single_elimination([f"t{i}" for i in range(n)])


def test_duplicate_seeds_raise() -> None:
    with pytest.raises(ValueError):
        generate_single_elimination(["a", "b", "a"])


def test_returns_bracket_instance() -> None:
    assert isinstance(generate_single_elimination(_seeds(4)), Bracket)
