"""Single-elimination bracket engine (PLAN.md §3.1 "Bracket engine").

Deterministic and pure: given a list of seeds (best-first), produce the full set
of rounds and matches for a single-elimination tournament supporting **2..16
teams with byes**.

Design (standard seeded single-elim):

* The bracket size is the next power of two >= the number of teams.
* Round 1 pairings follow the canonical "seed table" so that the #1 seed and the
  #2 seed can only meet in the final, #1..#4 are spread across quarters, etc. The
  table is built recursively: ``pattern(2) = [1, 2]`` and each larger pattern
  interleaves a seed with ``(2*size + 1 - seed)`` so every slot ``i`` is paired
  with ``bracket_size + 1 - seed``.
* **Byes** fill the ``bracket_size - n`` missing slots. Because the seed table
  pairs strong seeds against the weakest opponents, assigning the phantom
  (bye) seeds to the *highest* slot numbers (weakest seeds) means the byes land
  opposite the **top** seeds — i.e. the top seeds receive the byes, as required.
* A round-1 match whose opponent is a bye is auto-advanced: it has a single
  ``team`` (no opponent) and a pre-filled ``winner``; it still occupies a slot so
  the bracket tree stays a clean power-of-two binary tree.

The engine deals in opaque, hashable team identifiers (``Hashable``) — typically
``Team.id`` UUIDs — and never imports a web framework or DB layer.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field

MIN_TEAMS = 2
MAX_TEAMS = 16


@dataclass(frozen=True)
class BracketMatch:
    """One match (or auto-advanced bye) in the bracket.

    ``round_no`` is 1-based (round 1 = first round played). ``slot`` is the
    0-based index of the match within its round, ordered top-to-bottom so the
    winner of match ``slot`` in round ``r`` feeds match ``slot // 2`` in round
    ``r + 1``.

    For a normal match both ``team_a`` and ``team_b`` are set and ``winner`` is
    ``None`` until adjudicated. For a **bye**, ``is_bye`` is ``True``, the present
    team is in ``team_a``, ``team_b`` is ``None``, and ``winner`` is pre-filled
    with ``team_a``.
    """

    round_no: int
    slot: int
    team_a: Hashable | None
    team_b: Hashable | None
    is_bye: bool = False
    winner: Hashable | None = None

    @property
    def seeds_present(self) -> tuple[Hashable, ...]:
        return tuple(t for t in (self.team_a, self.team_b) if t is not None)


@dataclass(frozen=True)
class BracketRound:
    round_no: int
    matches: list[BracketMatch] = field(default_factory=list)


@dataclass(frozen=True)
class Bracket:
    """A full single-elimination bracket."""

    num_teams: int
    bracket_size: int
    rounds: list[BracketRound]

    @property
    def num_byes(self) -> int:
        return self.bracket_size - self.num_teams

    @property
    def total_matches(self) -> int:
        return sum(len(r.matches) for r in self.rounds)

    def all_matches(self) -> list[BracketMatch]:
        out: list[BracketMatch] = []
        for r in self.rounds:
            out.extend(r.matches)
        return out


def _next_power_of_two(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def _seed_order(bracket_size: int) -> list[int]:
    """Return the canonical single-elimination seed slot order.

    The returned list has length ``bracket_size``; reading it two-at-a-time gives
    the round-1 pairings (slot 0 vs slot 1, slot 2 vs slot 3, ...). Seeds are
    1-based. The standard recursive construction guarantees seed ``s`` is always
    paired against ``bracket_size + 1 - s`` in round 1 and that strong seeds are
    spread across the bracket halves/quarters.
    """

    order = [1, 2]
    while len(order) < bracket_size:
        size = len(order) * 2
        nxt: list[int] = []
        for s in order:
            nxt.append(s)
            nxt.append(size + 1 - s)
        order = nxt
    return order


def generate_single_elimination(seeds: Sequence[Hashable]) -> Bracket:
    """Build a single-elimination bracket from ``seeds`` (best seed first).

    Args:
        seeds: 2..16 distinct team identifiers ordered best-seed-first. Index 0 is
            the #1 seed.

    Returns:
        A fully populated :class:`Bracket`: round 1 contains real matches and
        auto-advanced byes; later rounds contain placeholder matches (both teams
        ``None``) that the advancement step fills in as results come in.

    Raises:
        ValueError: if the team count is outside 2..16 or seeds are not distinct.
    """

    n = len(seeds)
    if n < MIN_TEAMS or n > MAX_TEAMS:
        raise ValueError(f"team count must be in [{MIN_TEAMS}, {MAX_TEAMS}], got {n}")
    if len(set(seeds)) != n:
        raise ValueError("seeds must be distinct")

    bracket_size = _next_power_of_two(n)
    slot_order = _seed_order(bracket_size)

    # Map 1-based seed number -> team id, or None for a phantom (bye) seed. Phantom
    # seeds are the highest seed numbers (weakest), which the seed table pairs
    # against the strongest real seeds -> byes land on the top seeds.
    seed_to_team: dict[int, Hashable | None] = {}
    for seed_no in range(1, bracket_size + 1):
        seed_to_team[seed_no] = seeds[seed_no - 1] if seed_no <= n else None

    rounds: list[BracketRound] = []

    # ---- Round 1 -------------------------------------------------------- #
    first_round: list[BracketMatch] = []
    num_first_matches = bracket_size // 2
    for slot in range(num_first_matches):
        seed_a = slot_order[2 * slot]
        seed_b = slot_order[2 * slot + 1]
        team_a = seed_to_team[seed_a]
        team_b = seed_to_team[seed_b]

        if team_a is not None and team_b is not None:
            first_round.append(
                BracketMatch(round_no=1, slot=slot, team_a=team_a, team_b=team_b)
            )
        else:
            # Exactly one side is a phantom (the seed table never pairs two
            # phantoms together for n >= bracket_size/2 + 1, which always holds
            # since n > bracket_size/2 for any non-power-of-two count and equals
            # bracket_size for powers of two -> no byes at all).
            present = team_a if team_a is not None else team_b
            first_round.append(
                BracketMatch(
                    round_no=1,
                    slot=slot,
                    team_a=present,
                    team_b=None,
                    is_bye=True,
                    winner=present,
                )
            )
    rounds.append(BracketRound(round_no=1, matches=first_round))

    # ---- Subsequent rounds (placeholders) ------------------------------- #
    round_no = 2
    prev_count = num_first_matches
    while prev_count > 1:
        count = prev_count // 2
        matches = [
            BracketMatch(round_no=round_no, slot=slot, team_a=None, team_b=None)
            for slot in range(count)
        ]
        rounds.append(BracketRound(round_no=round_no, matches=matches))
        prev_count = count
        round_no += 1

    return Bracket(num_teams=n, bracket_size=bracket_size, rounds=rounds)
