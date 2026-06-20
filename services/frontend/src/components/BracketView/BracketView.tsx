// Single-elimination bracket visualizer (PLAN.md §3.4).
//
// PURE / PRESENTATIONAL: fed a flat list of matches; renders rounds left→right,
// matches top→bottom. Byes are labelled and the lone team auto-advances;
// (effective) winners are highlighted. All team names render as escaped React
// text — never raw HTML.
//
// Manual-override entry points: when `onOverride` is supplied, each undecided,
// playable match exposes a "Force winner" affordance that the caller wires to
// the results/dispute queue (results.recordWinner / admin_override).

import { Fragment } from "react";
import type { BracketMatch, BracketSlot } from "./types";
import { effectiveWinnerId, groupByRound, isBye, isWinningSlot } from "./bracket";

export interface BracketViewProps {
  /** Flat list of bracket matches (any order). */
  matches: ReadonlyArray<BracketMatch>;
  /**
   * Optional manual-override hook. Receives the match the admin wants to decide;
   * the caller routes this to the results/dispute override queue. When omitted,
   * no override controls render (read-only bracket).
   */
  onOverride?: (match: BracketMatch) => void;
  /** Accessible caption / label for the bracket region. */
  caption?: string;
}

function slotLabel(slot: BracketSlot | null): string {
  return slot ? slot.name : "—";
}

function BracketSlotRow({
  match,
  slot,
}: {
  match: BracketMatch;
  slot: BracketSlot | null;
}): JSX.Element {
  const winner = isWinningSlot(match, slot);
  return (
    <div
      data-bracket-slot
      data-winner={winner || undefined}
      data-empty={slot ? undefined : true}
    >
      <span data-bracket-team>{slotLabel(slot)}</span>
      {winner ? (
        <span data-bracket-winner-mark aria-label="winner">
          ✓
        </span>
      ) : null}
    </div>
  );
}

function BracketMatchCard({
  match,
  onOverride,
}: {
  match: BracketMatch;
  onOverride?: (match: BracketMatch) => void;
}): JSX.Element {
  const bye = isBye(match);
  const decided = effectiveWinnerId(match) !== null;
  // Overridable only when it's a real (two-sided) playable match that has not
  // been decided yet — the admin escape hatch for stuck/disputed results.
  const canOverride =
    Boolean(onOverride) && !bye && match.slotA !== null && match.slotB !== null && !decided;

  return (
    <li data-bracket-match data-bye={bye || undefined}>
      <div data-bracket-match-body>
        <BracketSlotRow match={match} slot={match.slotA} />
        <BracketSlotRow match={match} slot={match.slotB} />
      </div>
      {bye ? <p data-bracket-bye-note>Bye — auto-advances</p> : null}
      {canOverride ? (
        <button
          type="button"
          data-bracket-override
          onClick={() => onOverride?.(match)}
        >
          Force winner
        </button>
      ) : null}
    </li>
  );
}

export function BracketView({
  matches,
  onOverride,
  caption = "Tournament bracket",
}: BracketViewProps): JSX.Element {
  const rounds = groupByRound(matches);

  if (rounds.length === 0) {
    return (
      <div data-bracket role="group" aria-label={caption}>
        <p data-bracket-empty>No bracket yet. Start the tournament to generate matches.</p>
      </div>
    );
  }

  return (
    <div data-bracket role="group" aria-label={caption}>
      <ol data-bracket-rounds>
        {rounds.map((round, index) => (
          <Fragment key={round.round}>
            <li data-bracket-round>
              <h3 data-bracket-round-title>
                {index === rounds.length - 1 && round.matches.length === 1
                  ? "Final"
                  : `Round ${round.round}`}
              </h3>
              <ol data-bracket-round-matches>
                {round.matches.map((match) => (
                  <BracketMatchCard key={match.id} match={match} onOverride={onOverride} />
                ))}
              </ol>
            </li>
          </Fragment>
        ))}
      </ol>
    </div>
  );
}
