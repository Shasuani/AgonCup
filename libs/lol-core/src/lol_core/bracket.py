"""Single- and double-elimination bracket engine (PLAN.md §3.1 "Bracket engine").

Deterministic and pure: given a list of seeds (best-first), produce the full set
of rounds and matches for a single-elimination tournament supporting **2..16
teams with byes**.

The same module also provides :func:`generate_double_elimination` (PLAN.md §3.1;
§6 Phase 2 — "double-elim as a gated slice"), which reuses the single-elim
winners bracket and adds a losers ladder + grand final. See that function and
:class:`DoubleBracket` below for the full shape and the (error-prone) loser
routing rules.

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
            first_round.append(BracketMatch(round_no=1, slot=slot, team_a=team_a, team_b=team_b))
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


# ===================================================================== #
# Double elimination                                                    #
# ===================================================================== #
#
# Shape of a double-elimination event
# -----------------------------------
# For a bracket of ``B = next_power_of_two(n)`` slots (``k = log2(B)`` winners
# rounds):
#
# * **Winners bracket (WB)** — identical to a single-elimination bracket of size
#   ``B``: ``k`` rounds, WB round ``r`` (1-based) has ``B / 2**r`` matches. Round 1
#   may contain auto-advanced **byes** (top seeds), exactly as single-elim.
# * **Losers bracket (LB)** — a ``2 * (k - 1)`` round elimination ladder fed by
#   the losers dropping out of the winners bracket. It alternates two kinds of
#   rounds: *major* rounds (LB survivors play a freshly-dropped WB loser) and
#   *minor* rounds (LB survivors play each other). Match counts::
#
#       LB round 1, 2  -> B / 4
#       LB round 3, 4  -> B / 8
#       ...            -> ...
#       LB round 2k-3, 2k-2 -> 1
#
#   (For ``B == 2`` there is no losers bracket at all; the single WB match's loser
#   goes straight to the grand final.)
# * **Grand final (GF)** — one match: WB champion vs LB champion. A bracket
#   *reset* (the replay when the WB champion takes their first loss) is a *result
#   type* / replay of the GF match, not a separate bracket node — mirroring the
#   single-elim convention that walkover/forfeit is a result type, not a shape
#   (PLAN.md §3.1).
#
# Loser routing — the error-prone part
# ------------------------------------
# Every WB match (round ``r``, slot ``s``) routes its **loser** to a deterministic
# LB slot, surfaced on the match via ``drops_to_lb_round`` / ``drops_to_lb_slot``
# and tabulated on :class:`DoubleBracket.loser_routes`. The rules:
#
# * **WB round 1 losers -> LB round 1.** The ``B/2`` round-1 losers pair up into
#   ``B/4`` matches: WB-R1 matches ``2j`` and ``2j+1`` send their losers to LB-R1
#   slot ``j`` (sides ``"a"`` / ``"b"``). Two adjacent WB-R1 matches share a
#   parent in the winners tree, so their losers have *not* met — no rematch.
# * **WB round ``r`` losers (r >= 2) -> major LB round ``2*(r-1)``**, one dropped
#   loser per LB match (side ``"b"``; the LB survivor takes side ``"a"``). Major
#   round ``2*(r-1)`` has the same ``M = B / 2**r`` matches as WB round ``r``, and
#   the dropped losers are **mirrored** (reversed): WB-R``r`` slot ``s`` -> LB slot
#   ``M - 1 - s``.
#
#   Mirroring is the standard anti-rematch device. The LB survivor that reaches
#   major slot ``s`` descends (through the LB pairing rule "minor round pairs LB
#   slots ``2j``/``2j+1`` -> ``j``") from the *same* half/region of the winners
#   bracket as the WB-R``r`` loser of slot ``s``; reversing the drop sends each WB
#   loser to the *opposite* region's survivor, which is the pairing least likely
#   to be a rematch.
#
#   IMPORTANT — early WB/LB rematches cannot be eliminated entirely. A zero-rematch
#   double-elim drop is mathematically impossible for these sizes: the
#   worst-case minimum number of pre-grand-final rematches is exactly **1** for
#   ``B == 4`` and **2** for ``B == 8`` (proven by exhaustive search over all
#   outcomes — see the test module). The mirror routing here *achieves* that proven
#   minimum and, in particular, the canonical "chalk" bracket (seeds advance by
#   rank) hits the minimum rematch count. We therefore document and test the
#   achievable invariant (minimum-rematch, no *unnecessary* rematch) rather than an
#   unachievable absolute-zero one; a rematch that does occur is, at most, replayed
#   at the grand final, which is by design.
#
# A WB **bye** match has no loser (``loser`` is ``None``); the LB slot that would
# have received that loser instead carries a **bye** so the LB survivor
# auto-advances. Round-1 byes never pair two phantoms (same guarantee as
# single-elim), so LB round 1 byes are well defined.


@dataclass(frozen=True)
class LoserRoute:
    """Where the loser of one winners-bracket match drops in the losers bracket.

    ``wb_round`` / ``wb_slot`` identify the source WB match; its loser enters
    losers-bracket round ``lb_round`` at slot ``lb_slot``, filling ``side``
    (``"a"`` or ``"b"``) of that LB match. See the module-level "Loser routing"
    notes for the assignment rules.
    """

    wb_round: int
    wb_slot: int
    lb_round: int
    lb_slot: int
    side: str


@dataclass(frozen=True)
class DoubleBracketMatch(BracketMatch):
    """A match in a double-elimination bracket.

    Extends :class:`BracketMatch` (same ``round_no`` / ``slot`` / ``team_a`` /
    ``team_b`` / ``is_bye`` / ``winner`` semantics) with double-elim metadata:

    * ``bracket`` — one of ``"WB"`` (winners), ``"LB"`` (losers), ``"GF"`` (grand
      final).
    * ``drops_to_lb_round`` / ``drops_to_lb_slot`` — for WB matches only, the LB
      destination of this match's loser (``None`` for a bye, which has no loser,
      and for LB/GF matches whose loser is eliminated).
    """

    bracket: str = "WB"
    drops_to_lb_round: int | None = None
    drops_to_lb_slot: int | None = None


@dataclass(frozen=True)
class DoubleBracketRound(BracketRound):
    """A round within one of the double-elimination brackets.

    ``bracket`` is ``"WB"``, ``"LB"`` or ``"GF"``; ``round_no`` is 1-based within
    that bracket. ``matches`` is narrowed to :class:`DoubleBracketMatch`.
    """

    matches: list[DoubleBracketMatch] = field(default_factory=list)  # type: ignore[assignment]
    bracket: str = "WB"


@dataclass(frozen=True)
class DoubleBracket:
    """A full double-elimination bracket: winners + losers + grand final.

    Mirrors :class:`Bracket` on the winners side and adds the losers ladder and
    grand final. ``loser_routes`` is the explicit per-WB-match loser-routing table
    (see the module "Loser routing" notes) keyed by ``(wb_round, wb_slot)``.
    """

    num_teams: int
    bracket_size: int
    winners: list[DoubleBracketRound]
    losers: list[DoubleBracketRound]
    grand_final: DoubleBracketMatch
    loser_routes: dict[tuple[int, int], LoserRoute] = field(default_factory=dict)

    @property
    def num_byes(self) -> int:
        return self.bracket_size - self.num_teams

    @property
    def total_matches(self) -> int:
        wb = sum(len(r.matches) for r in self.winners)
        lb = sum(len(r.matches) for r in self.losers)
        return wb + lb + 1  # + grand final

    def all_matches(self) -> list[DoubleBracketMatch]:
        out: list[DoubleBracketMatch] = []
        for r in self.winners:
            out.extend(r.matches)
        for r in self.losers:
            out.extend(r.matches)
        out.append(self.grand_final)
        return out

    @property
    def winners_bracket(self) -> Bracket:
        """The winners side as a plain single-elim :class:`Bracket` view."""

        plain_rounds = [
            BracketRound(
                round_no=r.round_no,
                matches=[
                    BracketMatch(
                        round_no=m.round_no,
                        slot=m.slot,
                        team_a=m.team_a,
                        team_b=m.team_b,
                        is_bye=m.is_bye,
                        winner=m.winner,
                    )
                    for m in r.matches
                ],
            )
            for r in self.winners
        ]
        return Bracket(
            num_teams=self.num_teams,
            bracket_size=self.bracket_size,
            rounds=plain_rounds,
        )


def _lb_round_match_count(bracket_size: int, lb_round: int) -> int:
    """Number of matches in losers-bracket round ``lb_round`` (1-based).

    For ``B = bracket_size`` and ``k = log2(B)`` there are ``2*(k-1)`` LB rounds
    in (minor, major) pairs sharing a count; the count halves every pair, starting
    at ``B / 4`` (LB rounds 1,2), then ``B / 8`` (rounds 3,4), and so on.
    """

    pair = (lb_round - 1) // 2  # 0 for rounds {1,2}, 1 for {3,4}, ...
    return bracket_size // (4 << pair)


def _build_losers_rounds(
    bracket_size: int,
) -> tuple[list[DoubleBracketRound], dict[tuple[int, int], LoserRoute]]:
    """Build the LB placeholder rounds and the loser-routing table.

    Pure and structural: depends only on ``bracket_size`` (a power of two).
    Returns the LB rounds (all placeholder matches — teams filled by advancement
    as results arrive) plus the ``(wb_round, wb_slot) -> LoserRoute`` map.
    """

    k = bracket_size.bit_length() - 1  # log2(B); B is a power of two
    routes: dict[tuple[int, int], LoserRoute] = {}

    if k < 2:
        # B == 2: no losers bracket; the lone WB-final loser drops straight to the
        # grand final, handled by the caller.
        return [], routes

    num_lb_rounds = 2 * (k - 1)
    lb_rounds: list[DoubleBracketRound] = []
    for lb_round in range(1, num_lb_rounds + 1):
        count = _lb_round_match_count(bracket_size, lb_round)
        matches = [
            DoubleBracketMatch(
                round_no=lb_round,
                slot=slot,
                team_a=None,
                team_b=None,
                bracket="LB",
            )
            for slot in range(count)
        ]
        lb_rounds.append(DoubleBracketRound(round_no=lb_round, matches=matches, bracket="LB"))

    # ---- WB round 1 losers -> LB round 1 -------------------------------- #
    # WB round 1 has B/2 matches; adjacent pairs (2j, 2j+1) meet in LB-R1 slot j.
    # A bye WB match has no loser, so it contributes no route (its LB side stays a
    # bye and the other dropped loser auto-advances).
    wb_r1_count = bracket_size // 2
    for s in range(wb_r1_count):
        lb_slot = s // 2
        side = "a" if s % 2 == 0 else "b"
        routes[(1, s)] = LoserRoute(wb_round=1, wb_slot=s, lb_round=1, lb_slot=lb_slot, side=side)

    # ---- WB round r (>=2) losers -> major LB round 2*(r-1) -------------- #
    # Mirror (reverse) mapping; the dropped loser fills side "b" and the LB survivor
    # occupies side "a". See the module "Loser routing" notes: mirroring sends each
    # WB loser to the opposite-region LB survivor and achieves the proven minimum
    # number of (otherwise unavoidable) early rematches.
    for r in range(2, k + 1):
        wb_count = bracket_size // (2**r)
        major_lb_round = 2 * (r - 1)
        for s in range(wb_count):
            lb_slot = wb_count - 1 - s  # mirror
            routes[(r, s)] = LoserRoute(
                wb_round=r, wb_slot=s, lb_round=major_lb_round, lb_slot=lb_slot, side="b"
            )

    return lb_rounds, routes


def generate_double_elimination(seeds: Sequence[Hashable]) -> DoubleBracket:
    """Build a double-elimination bracket from ``seeds`` (best seed first).

    The winners bracket is the canonical single-elimination bracket of size
    ``next_power_of_two(n)`` (byes auto-advance the top seeds); a losers ladder of
    ``2*(log2 B - 1)`` rounds is generated as placeholders together with the
    explicit loser-routing table, and a single grand-final match closes the event.
    See the module "Double elimination" / "Loser routing" notes for the full shape
    (PLAN.md §3.1 "Bracket engine").

    Args:
        seeds: 2..16 distinct team identifiers ordered best-seed-first. Index 0 is
            the #1 seed.

    Returns:
        A fully populated :class:`DoubleBracket`. Round-1 winners matches contain
        real teams and auto-advanced byes; every later WB match, every LB match,
        and the grand final are placeholders (teams ``None``) that the advancement
        step fills as results — and loser drops — come in.

    Raises:
        ValueError: if the team count is outside 2..16 or seeds are not distinct.
    """

    n = len(seeds)
    if n < MIN_TEAMS or n > MAX_TEAMS:
        raise ValueError(f"team count must be in [{MIN_TEAMS}, {MAX_TEAMS}], got {n}")
    if len(set(seeds)) != n:
        raise ValueError("seeds must be distinct")

    bracket_size = _next_power_of_two(n)

    # ---- Winners bracket: reuse the single-elim engine, then tag -------- #
    se = generate_single_elimination(seeds)
    lb_rounds, routes = _build_losers_rounds(bracket_size)

    # Byes have no loser to drop; drop their (structural) routes so the table
    # reflects only real drops and stays consistent with each match's
    # ``drops_to_lb_*`` fields.
    for rnd in se.rounds:
        for m in rnd.matches:
            if m.is_bye:
                routes.pop((m.round_no, m.slot), None)

    winners: list[DoubleBracketRound] = []
    for rnd in se.rounds:
        matches: list[DoubleBracketMatch] = []
        for m in rnd.matches:
            route = routes.get((m.round_no, m.slot))
            # A bye has no loser to drop, so it carries no LB destination.
            drop_round = None if (m.is_bye or route is None) else route.lb_round
            drop_slot = None if (m.is_bye or route is None) else route.lb_slot
            matches.append(
                DoubleBracketMatch(
                    round_no=m.round_no,
                    slot=m.slot,
                    team_a=m.team_a,
                    team_b=m.team_b,
                    is_bye=m.is_bye,
                    winner=m.winner,
                    bracket="WB",
                    drops_to_lb_round=drop_round,
                    drops_to_lb_slot=drop_slot,
                )
            )
        winners.append(DoubleBracketRound(round_no=rnd.round_no, matches=matches, bracket="WB"))

    # ---- Grand final ---------------------------------------------------- #
    grand_final = DoubleBracketMatch(
        round_no=1,
        slot=0,
        team_a=None,
        team_b=None,
        bracket="GF",
    )

    return DoubleBracket(
        num_teams=n,
        bracket_size=bracket_size,
        winners=winners,
        losers=lb_rounds,
        grand_final=grand_final,
        loser_routes=routes,
    )
