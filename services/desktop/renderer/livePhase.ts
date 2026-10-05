// The match phase as the player should see it: the backend's lobby state,
// pushed forward by what the League client on this machine proves.
//
// The backend advances a lobby only from the automatic host's heartbeat, and only
// while the lobby is in an automatic state. A lobby opened by hand is recorded as
// `host_lost` and stays there for good (PLAN.md §3.6.1: a match that fell back to
// manual stays manual), and an automatic host whose create failed sits in
// `awaiting_create`. Both live under the "Host" step, so the Live match screen
// said "Host" through champion select and the whole game, and the scoreboard —
// the items everyone is building, K/D/A — never appeared. The client on this
// machine knows better: its champ-select board and its `:2999` live game are
// read anyway. This module lets them speak. Display-only, like both sources.
import type { ChampSelectBoard, LiveGame, MyMatch } from './bridge';

/** The visible milestones of a match, in order, and which phases sit at each. */
export const STEPS: { label: string; phases: string[] }[] = [
  { label: 'Host', phases: ['assigned_host', 'awaiting_create', 'host_lost'] },
  { label: 'Lobby', phases: ['lobby_open'] },
  { label: 'Players', phases: ['filling', 'ready'] },
  { label: 'Champ select', phases: ['starting'] },
  { label: 'In game', phases: ['in_game'] },
  { label: 'Result', phases: ['result_pending', 'ended'] },
];

export function stepIndex(state: string | null): number {
  if (state === null) return -1;
  return STEPS.findIndex((s) => s.phases.includes(state));
}

/** True once the lobby demonstrably exists in the League client. */
export function lobbyExists(state: string | null): boolean {
  return state === 'lobby_open' || state === 'filling' || state === 'ready' || isPlaying(state);
}

/** True from champion select until the result is recorded. */
export function isPlaying(state: string | null): boolean {
  return state === 'starting' || state === 'in_game' || state === 'result_pending';
}

/** What the League client on this machine proves about a game right now. */
export type LocalPhase = 'starting' | 'in_game' | 'result_pending' | null;

export function localPhase(board: ChampSelectBoard | null, live: LiveGame | null): LocalPhase {
  // The two pollers tick a few seconds apart, so for a moment both can be set:
  // a running game outranks a board the client has not dropped yet, and a fresh
  // champ select outranks the frozen banner of the game before it.
  if (live !== null && live.status === 'in_game') return 'in_game';
  if (board !== null) return 'starting';
  if (live !== null) return 'result_pending';
  return null;
}

/**
 * Whether a game in this `:2999` mode can be a match of `matchMode` at all — a
 * player's own ARAM must not light up their 5v5 card. Unknown modes prove
 * nothing either way.
 */
function modeFits(matchMode: string, liveMode: string | null): boolean {
  const mode = (liveMode ?? '').toUpperCase();
  if (mode === 'ARAM') return matchMode === 'aram_1v1';
  if (mode === 'CLASSIC') return matchMode !== 'aram_1v1';
  if (mode === 'PRACTICETOOL' || mode === 'TUTORIAL') return false;
  return true;
}

/** A match whose lobby exists in some form and is not over — the ones a local game could belong to. */
export function isOpenMatch(m: MyMatch): boolean {
  return m.lobbyState !== null && m.lobbyState !== 'ended' && m.lobbyState !== 'aborted';
}

/**
 * The open match the game on this machine belongs to. A player is in one game
 * at a time, so it is the open match that fits the live game's mode — the one
 * the backend already knows is furthest along, when several do.
 */
export function pickPlayingMatch(
  matches: readonly MyMatch[],
  phase: LocalPhase,
  liveMode: string | null,
): MyMatch | undefined {
  if (phase === null) return undefined;
  return matches
    .filter((m) => isOpenMatch(m) && modeFits(m.gameMode, liveMode))
    .reduce<MyMatch | undefined>(
      (best, m) => (best === undefined || stepIndex(m.lobbyState) > stepIndex(best.lobbyState) ? m : best),
      undefined,
    );
}

/** The furthest of what the backend recorded for `m` and what this machine shows, when the game is `m`'s. */
export function effectiveState(m: MyMatch, phase: LocalPhase, playing: MyMatch | undefined): string | null {
  if (phase === null || playing === undefined || playing.matchId !== m.matchId) return m.lobbyState;
  return stepIndex(phase) > stepIndex(m.lobbyState) ? phase : m.lobbyState;
}
