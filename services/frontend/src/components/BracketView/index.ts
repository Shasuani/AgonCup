// Public surface of the reusable bracket visualizer.
export { BracketView } from "./BracketView";
export type { BracketViewProps } from "./BracketView";
export type { BracketMatch, BracketRound, BracketSlot } from "./types";
export { groupByRound, isBye, effectiveWinnerId, isWinningSlot } from "./bracket";
