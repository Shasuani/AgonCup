// Presentational data model for the single-elimination bracket visualizer.
//
// These types are intentionally LOCAL to BracketView: the component is pure and
// is fed match data by its caller. The backend does not (yet) expose a
// GET matches endpoint via lol-clients-ts, so screens that have match data
// (e.g. from a future feed) map it into this shape. Keeping the model here keeps
// BracketView reusable and decoupled from any transport.

/** A competitor slot in a match. A bye/unfilled slot is represented by `null`. */
export interface BracketSlot {
  /** Stable team id (used for winner highlighting + keys). */
  teamId: string;
  /** Human-readable team name (rendered as escaped text). */
  name: string;
}

/** A single bracket match between two slots (either may be a bye). */
export interface BracketMatch {
  /** Stable match id. */
  id: string;
  /** 1-based round number (round 1 = first round). */
  round: number;
  /**
   * Position of the match WITHIN its round, 0-based. Drives vertical ordering
   * and the implicit feed into the next round (slot pairs collapse upward).
   */
  position: number;
  /** "Top" / first competitor slot, or null for an empty/bye slot. */
  slotA: BracketSlot | null;
  /** "Bottom" / second competitor slot, or null for an empty/bye slot. */
  slotB: BracketSlot | null;
  /**
   * The decided winner's team id, or null if undecided. When exactly one slot
   * is filled the match is a BYE: that slot auto-advances and is treated as the
   * winner for highlighting.
   */
  winnerTeamId: string | null;
}

/** A round (column) of the bracket: its number + ordered matches. */
export interface BracketRound {
  round: number;
  matches: BracketMatch[];
}
