// Pure helpers for the bracket visualizer: group flat matches into rounds and
// classify slots (winner / loser / bye). No React, no IO — unit-testable.

import type { BracketMatch, BracketRound, BracketSlot } from "./types";

/**
 * Group a flat match list into ordered rounds. Rounds are sorted ascending by
 * round number; matches within a round are sorted ascending by position. Input
 * is not mutated.
 */
export function groupByRound(matches: ReadonlyArray<BracketMatch>): BracketRound[] {
  const byRound = new Map<number, BracketMatch[]>();
  for (const match of matches) {
    const bucket = byRound.get(match.round);
    if (bucket) {
      bucket.push(match);
    } else {
      byRound.set(match.round, [match]);
    }
  }
  return [...byRound.keys()]
    .sort((a, b) => a - b)
    .map((round) => ({
      round,
      matches: [...(byRound.get(round) ?? [])].sort((a, b) => a.position - b.position),
    }));
}

/**
 * A match is a BYE when exactly one slot is filled. The filled team
 * auto-advances (no opponent to play).
 */
export function isBye(match: BracketMatch): boolean {
  const filled = (match.slotA ? 1 : 0) + (match.slotB ? 1 : 0);
  return filled === 1;
}

/**
 * The effective winning team id for highlighting: the explicit `winnerTeamId`
 * if present, otherwise the lone team in a bye. `null` when undecided.
 */
export function effectiveWinnerId(match: BracketMatch): string | null {
  if (match.winnerTeamId) return match.winnerTeamId;
  if (isBye(match)) {
    return (match.slotA ?? match.slotB)?.teamId ?? null;
  }
  return null;
}

/** True when `slot` is the (effective) winner of `match`. */
export function isWinningSlot(match: BracketMatch, slot: BracketSlot | null): boolean {
  if (!slot) return false;
  return effectiveWinnerId(match) === slot.teamId;
}
