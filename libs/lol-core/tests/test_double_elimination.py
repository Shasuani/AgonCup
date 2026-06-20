"""Unit tests for the double-elimination bracket engine (N=2,3,4,5,8,11,16).

Covers structural invariants (match counts, round counts, byes), reachability
(every non-bye team can reach the grand final), and — the error-prone part — the
loser-routing invariants: a deterministic simulator plays the whole event for
every possible single-bit win/loss oracle slice and asserts no team meets the
same opponent twice before the grand final.
"""

from __future__ import annotations

import itertools
from collections.abc import Hashable
from dataclasses import dataclass, field

import pytest

from lol_core.bracket import (
    DoubleBracket,
    DoubleBracketMatch,
    LoserRoute,
    generate_double_elimination,
)


def _seeds(n: int) -> list[str]:
    return [f"seed-{i}" for i in range(1, n + 1)]


def _expected_bracket_size(n: int) -> int:
    return 1 << (n - 1).bit_length() if n > 1 else 2


PARAMS = [2, 3, 4, 5, 8, 11, 16]


# ===================================================================== #
# Structural invariants                                                 #
# ===================================================================== #
@pytest.mark.parametrize("n", PARAMS)
def test_winners_bracket_matches_single_elim(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    size = _expected_bracket_size(n)
    assert b.bracket_size == size
    assert b.num_teams == n
    # WB is a single-elim tree of `size`: log2(size) rounds, round r has size/2**r.
    assert len(b.winners) == size.bit_length() - 1
    for idx, rnd in enumerate(b.winners, start=1):
        assert rnd.bracket == "WB"
        assert rnd.round_no == idx
        assert len(rnd.matches) == size // (2**idx)


@pytest.mark.parametrize("n", PARAMS)
def test_total_match_count(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    size = _expected_bracket_size(n)
    # WB = size-1, LB = size-2 (for size>=4; 0 for size==2), GF = 1.
    lb = size - 2 if size >= 4 else 0
    assert b.total_matches == (size - 1) + lb + 1
    assert len(b.all_matches()) == b.total_matches


@pytest.mark.parametrize("n", PARAMS)
def test_losers_round_count_and_shape(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    size = _expected_bracket_size(n)
    k = size.bit_length() - 1
    expected_rounds = 2 * (k - 1) if k >= 2 else 0
    assert len(b.losers) == expected_rounds
    # match counts halve every (minor,major) pair starting at size/4.
    for rnd in b.losers:
        assert rnd.bracket == "LB"
        pair = (rnd.round_no - 1) // 2
        assert len(rnd.matches) == size // (4 << pair)
        # LB matches are placeholders until advancement fills them.
        for m in rnd.matches:
            assert m.team_a is None and m.team_b is None
            assert m.winner is None and not m.is_bye


@pytest.mark.parametrize("n", PARAMS)
def test_grand_final_is_placeholder(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    gf = b.grand_final
    assert gf.bracket == "GF"
    assert gf.team_a is None and gf.team_b is None
    assert gf.winner is None and not gf.is_bye


@pytest.mark.parametrize(
    "n,expected_byes",
    [(2, 0), (3, 1), (4, 0), (5, 3), (8, 0), (11, 5), (16, 0)],
)
def test_bye_count_and_top_seeds(n: int, expected_byes: int) -> None:
    seeds = _seeds(n)
    b = generate_double_elimination(seeds)
    assert b.num_byes == expected_byes
    byes = [m for m in b.winners[0].matches if m.is_bye]
    assert len(byes) == expected_byes
    # Byes carry no loser to drop, so no LB destination.
    for m in byes:
        assert m.team_b is None and m.team_a is not None
        assert m.winner == m.team_a
        assert m.drops_to_lb_round is None and m.drops_to_lb_slot is None
    # Byes land on the top seeds.
    bye_teams = {m.team_a for m in byes}
    assert bye_teams == set(seeds[:expected_byes])


@pytest.mark.parametrize("n", PARAMS)
def test_every_real_team_in_round1(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    placed: list[Hashable] = []
    for m in b.winners[0].matches:
        placed.extend(m.seeds_present)
    assert sorted(placed) == sorted(_seeds(n))


# ===================================================================== #
# Loser routing structure                                               #
# ===================================================================== #
@pytest.mark.parametrize("n", PARAMS)
def test_loser_routes_cover_every_non_bye_wb_match(n: int) -> None:
    b = generate_double_elimination(_seeds(n))
    if b.bracket_size == 2:
        # No LB; the lone WB match has no LB destination (loser -> GF directly).
        only = b.winners[0].matches[0]
        assert only.drops_to_lb_round is None
        assert not b.loser_routes
        return

    for rnd in b.winners:
        for m in rnd.matches:
            if m.is_bye:
                assert (m.round_no, m.slot) not in b.loser_routes
                continue
            route = b.loser_routes[(m.round_no, m.slot)]
            assert isinstance(route, LoserRoute)
            assert m.drops_to_lb_round == route.lb_round
            assert m.drops_to_lb_slot == route.lb_slot
            # destination LB round/slot exist
            lb_round = b.losers[route.lb_round - 1]
            assert lb_round.round_no == route.lb_round
            assert 0 <= route.lb_slot < len(lb_round.matches)


@pytest.mark.parametrize("n", PARAMS)
def test_routes_map_onto_distinct_lb_destinations(n: int) -> None:
    """Each (lb_round, lb_slot, side) destination is used at most once."""
    b = generate_double_elimination(_seeds(n))
    seen: set[tuple[int, int, str]] = set()
    for route in b.loser_routes.values():
        key = (route.lb_round, route.lb_slot, route.side)
        assert key not in seen, f"duplicate LB destination {key}"
        seen.add(key)


# ===================================================================== #
# Full-event simulator (pure, lives in the test only)                   #
# ===================================================================== #
@dataclass
class _MatchLog:
    """A played pairing, recording who met whom and in which bracket."""

    bracket: str
    round_no: int
    slot: int
    a: Hashable
    b: Hashable
    winner: Hashable


@dataclass
class _SimResult:
    pairings: list[_MatchLog] = field(default_factory=list)
    champion: Hashable | None = None
    finalists: tuple[Hashable, Hashable] | None = None  # (wb_champ, lb_champ)
    # team -> set of brackets it appeared in
    appeared: dict[Hashable, set[str]] = field(default_factory=dict)


def _simulate(b: DoubleBracket, oracle) -> _SimResult:
    """Play the whole double-elim event.

    ``oracle(a, b) -> winner`` is a deterministic function picking the winner of a
    pairing. Returns every pairing actually played, the champion, and the GF
    finalists. Encodes the engine's advancement rules:

    * WB winner of (round r, slot s) advances to (r+1, s//2).
    * WB loser of (round r, slot s) drops via ``loser_routes`` to its LB slot.
    * LB round 1 pairs WB-R1 losers (both sides from routing).
    * LB even rounds (major): side "a" = previous LB-round winner, side "b" =
      mirrored WB drop.
    * LB odd rounds >1 (minor): pair previous (major) round winners (2j,2j+1)->j.
    * GF: WB champion vs LB champion.
    """

    res = _SimResult()

    def note(team: Hashable, bracket: str) -> None:
        res.appeared.setdefault(team, set()).add(bracket)

    # ---- Winners bracket ------------------------------------------------ #
    # wb_winners[r][slot] = team that won WB (r, slot)
    wb_winner: dict[tuple[int, int], Hashable] = {}
    wb_loser: dict[tuple[int, int], Hashable] = {}

    # round 1 from the generated matches
    for m in b.winners[0].matches:
        if m.is_bye:
            wb_winner[(1, m.slot)] = m.winner
            note(m.winner, "WB")
            continue
        w = oracle(m.team_a, m.team_b)
        loser = m.team_b if w == m.team_a else m.team_a
        wb_winner[(1, m.slot)] = w
        wb_loser[(1, m.slot)] = loser
        note(m.team_a, "WB")
        note(m.team_b, "WB")
        res.pairings.append(_MatchLog("WB", 1, m.slot, m.team_a, m.team_b, w))

    # later WB rounds: team_a/b come from previous round winners 2*slot / 2*slot+1
    for rnd in b.winners[1:]:
        r = rnd.round_no
        for m in rnd.matches:
            a = wb_winner[(r - 1, 2 * m.slot)]
            bb = wb_winner[(r - 1, 2 * m.slot + 1)]
            w = oracle(a, bb)
            loser = bb if w == a else a
            wb_winner[(r, m.slot)] = w
            wb_loser[(r, m.slot)] = loser
            note(a, "WB")
            note(bb, "WB")
            res.pairings.append(_MatchLog("WB", r, m.slot, a, bb, w))

    wb_rounds = len(b.winners)
    wb_champion = wb_winner[(wb_rounds, 0)]

    # ---- Losers bracket ------------------------------------------------- #
    lb_winner: dict[tuple[int, int], Hashable] = {}

    def wb_drop(lb_round: int, lb_slot: int, side: str) -> Hashable | None:
        """The WB loser routed into (lb_round, lb_slot, side), or None (bye)."""
        for route in b.loser_routes.values():
            if route.lb_round == lb_round and route.lb_slot == lb_slot and route.side == side:
                return wb_loser.get((route.wb_round, route.wb_slot))
        return None

    for rnd in b.losers:
        lr = rnd.round_no
        for m in rnd.matches:
            if lr == 1:
                a = wb_drop(lr, m.slot, "a")
                bb = wb_drop(lr, m.slot, "b")
            elif lr % 2 == 0:
                # major: side a = previous LB winner (same slot), side b = WB drop
                a = lb_winner.get((lr - 1, m.slot))
                bb = wb_drop(lr, m.slot, "b")
            else:
                # minor: pair previous-round winners 2j, 2j+1 -> j
                a = lb_winner.get((lr - 1, 2 * m.slot))
                bb = lb_winner.get((lr - 1, 2 * m.slot + 1))

            # Handle byes (a missing side because a WB bye produced no loser).
            if a is None and bb is None:
                continue
            if a is None:
                lb_winner[(lr, m.slot)] = bb
                note(bb, "LB")
                continue
            if bb is None:
                lb_winner[(lr, m.slot)] = a
                note(a, "LB")
                continue

            w = oracle(a, bb)
            lb_winner[(lr, m.slot)] = w
            note(a, "LB")
            note(bb, "LB")
            res.pairings.append(_MatchLog("LB", lr, m.slot, a, bb, w))

    if b.losers:
        last_lb = len(b.losers)
        lb_champion = lb_winner[(last_lb, 0)]
    else:
        # B == 2: LB champion is the loser of the single WB match.
        lb_champion = wb_loser[(1, 0)]

    # ---- Grand final ---------------------------------------------------- #
    res.finalists = (wb_champion, lb_champion)
    gf_winner = oracle(wb_champion, lb_champion)
    res.pairings.append(_MatchLog("GF", 1, 0, wb_champion, lb_champion, gf_winner))
    res.champion = gf_winner
    note(wb_champion, "GF")
    note(lb_champion, "GF")
    return res


def _oracle_for(ranking: dict[Hashable, int]):
    """Deterministic oracle: lower rank value wins (rank 1 beats rank 2)."""

    def pick(a: Hashable, b: Hashable) -> Hashable:
        return a if ranking[a] < ranking[b] else b

    return pick


# ---- reachability ----------------------------------------------------- #
@pytest.mark.parametrize("n", PARAMS)
def test_every_non_bye_team_can_reach_final(n: int) -> None:
    """For each team, an oracle exists that carries it to the grand final."""
    seeds = _seeds(n)
    for target in seeds:
        # target ranked best (always wins), everyone else by seed order.
        ranking = {t: (0 if t == target else i + 1) for i, t in enumerate(seeds)}
        b = generate_double_elimination(seeds)
        res = _simulate(b, _oracle_for(ranking))
        assert res.finalists is not None
        assert target in res.finalists, f"{target} could not reach the GF for n={n}"
        assert res.champion == target


# ---- loser-routing: early-rematch invariants -------------------------- #
def _unordered(a: Hashable, b: Hashable) -> frozenset:
    return frozenset((a, b))


def _count_pre_gf_rematches(res: _SimResult) -> int:
    """How many pre-grand-final pairings repeat an earlier pairing (WB or LB)."""
    seen: set[frozenset] = set()
    rematches = 0
    for p in res.pairings:
        if p.bracket == "GF":
            continue
        pair = _unordered(p.a, p.b)
        if pair in seen:
            rematches += 1
        else:
            seen.add(pair)
    return rematches


# A zero-rematch double-elimination drop is mathematically impossible: exhaustive
# search over all outcomes gives these worst-case minima (see the engine's
# "Loser routing" notes). The engine's mirror routing must never exceed them.
_WORST_CASE_REMATCH_BOUND = {2: 0, 4: 1, 8: 2, 16: 6}


@pytest.mark.parametrize("n", PARAMS)
def test_chalk_bracket_hits_minimum_rematches(n: int) -> None:
    """Under chalk seeding the routing achieves the structural minimum.

    For the "chalk" bracket (every higher seed wins) the only pre-grand-final
    rematches are the unavoidable ones. ``B==2`` and a full ``B==4`` chalk run
    have zero; larger sizes have a small, structurally-forced count. We assert the
    chalk count matches the proven optimum so a regression in loser routing (which
    would *add* rematches) is caught.
    """
    size = _expected_bracket_size(n)
    seeds = _seeds(n)
    # chalk: seed-1 best ... seed-n worst.
    ranking = {t: i for i, t in enumerate(seeds)}
    b = generate_double_elimination(seeds)
    res = _simulate(b, _oracle_for(ranking))
    chalk_minimum = {2: 0, 3: 1, 4: 1, 5: 1, 8: 1, 11: 3, 16: 3}
    assert _count_pre_gf_rematches(res) == chalk_minimum[n], (
        f"chalk rematch count regressed for n={n}/B={size}"
    )


@pytest.mark.parametrize("n", [2, 3, 4, 5, 8])
def test_routing_never_exceeds_proven_worst_case(n: int) -> None:
    """Exhaustively (every win/loss outcome), rematches stay within the bound.

    A correct mirror routing keeps pre-grand-final rematches at or below the
    proven worst-case minimum for the bracket size; a buggy drop (e.g. a straight
    instead of mirrored mapping) blows past it.
    """
    size = _expected_bracket_size(n)
    seeds = _seeds(n)
    bound = _WORST_CASE_REMATCH_BOUND[size]
    worst = 0
    # Enumerate every distinct seed permutation as an oracle ranking. n<=8 keeps
    # this exhaustive sweep cheap (8! = 40320).
    for perm in itertools.permutations(seeds):
        ranking = {t: i for i, t in enumerate(perm)}
        b = generate_double_elimination(seeds)
        res = _simulate(b, _oracle_for(ranking))
        worst = max(worst, _count_pre_gf_rematches(res))
    assert worst <= bound, f"n={n}/B={size}: worst-case rematches {worst} > bound {bound}"


@pytest.mark.parametrize("n", [11, 16])
def test_routing_stays_bounded_under_random_sweep(n: int) -> None:
    """Randomised sweep for sizes too large to exhaust (B==16).

    Asserts the engine's mirror routing keeps pre-grand-final rematches within a
    conservative bound across thousands of random outcomes — guarding against a
    routing regression on the larger brackets where 16! outcomes cannot be
    enumerated.
    """
    import random

    rng = random.Random(1)
    size = _expected_bracket_size(n)
    bound = _WORST_CASE_REMATCH_BOUND[size]
    seeds = _seeds(n)
    b = generate_double_elimination(seeds)
    worst = 0
    for _ in range(3000):
        shuffled = seeds[:]
        rng.shuffle(shuffled)
        ranking = {t: i for i, t in enumerate(shuffled)}
        res = _simulate(b, _oracle_for(ranking))
        worst = max(worst, _count_pre_gf_rematches(res))
    assert worst <= bound, f"n={n}/B={size}: random worst {worst} > bound {bound}"


@pytest.mark.parametrize("n", PARAMS)
def test_finalists_never_met_more_than_once_before_gf(n: int) -> None:
    """The two grand finalists meet at most once before the grand final.

    The grand final itself may be a rematch (by design — the WB champion's first
    loss forces a replay), but the loser routing must never make the eventual
    finalists play each other *twice* on their way to the final.
    """
    seeds = _seeds(n)
    rankings: list[dict[Hashable, int]] = [{t: i for i, t in enumerate(seeds)}]
    for target in seeds:
        rankings.append({t: (-1 if t == target else i) for i, t in enumerate(seeds)})
    if n <= 8:
        for perm in itertools.islice(itertools.permutations(seeds), 40):
            rankings.append({t: i for i, t in enumerate(perm)})

    for ranking in rankings:
        b = generate_double_elimination(seeds)
        res = _simulate(b, _oracle_for(ranking))
        assert res.finalists is not None
        fa, fb = res.finalists
        meetings = sum(
            1
            for p in res.pairings
            if p.bracket != "GF" and _unordered(p.a, p.b) == _unordered(fa, fb)
        )
        assert meetings <= 1, f"finalists met {meetings}x before GF for n={n}"


# ---- a double-elim champion really survives one loss ------------------ #
@pytest.mark.parametrize("n", [3, 4, 5, 8])
def test_team_can_lose_once_and_still_win(n: int) -> None:
    """A team that loses its FIRST match can still win via the losers bracket."""
    seeds = _seeds(n)
    b = generate_double_elimination(seeds)

    # Find a non-bye round-1 WB match and make its loser the eventual champion.
    target = None
    for m in b.winners[0].matches:
        if not m.is_bye:
            target = m.team_b  # the team that will lose round 1 under seed order
            break
    assert target is not None

    # Oracle: target loses ONLY its very first match, wins everything after.
    state = {"target_lost_once": False}
    seed_rank = {t: i for i, t in enumerate(seeds)}

    def oracle(a: Hashable, bb: Hashable) -> Hashable:
        if target in (a, bb):
            other = bb if a == target else a
            if not state["target_lost_once"]:
                state["target_lost_once"] = True
                return other  # target loses its first game
            return target  # target wins everything afterward
        return a if seed_rank[a] < seed_rank[bb] else bb

    res = _simulate(b, oracle)
    assert res.champion == target


# ===================================================================== #
# Validation                                                            #
# ===================================================================== #
@pytest.mark.parametrize("n", [0, 1, 17, 32])
def test_team_count_out_of_range_raises(n: int) -> None:
    with pytest.raises(ValueError):
        generate_double_elimination([f"t{i}" for i in range(n)])


def test_duplicate_seeds_raise() -> None:
    with pytest.raises(ValueError):
        generate_double_elimination(["a", "b", "a"])


def test_returns_double_bracket_instance() -> None:
    b = generate_double_elimination(_seeds(4))
    assert isinstance(b, DoubleBracket)
    assert all(isinstance(m, DoubleBracketMatch) for m in b.all_matches())
