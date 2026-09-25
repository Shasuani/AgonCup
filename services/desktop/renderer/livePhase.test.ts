/**
 * Unit tests for the local-phase merge (node:test; pure, no React rendering).
 * The bug this guards: a by-hand lobby never leaves `host_lost` server-side, so
 * the Live match screen sat on "Host" through champion select and the game and
 * never showed the scoreboard.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { ChampSelectBoard, LiveGame, MyMatch } from './bridge';
import { effectiveState, localPhase, pickPlayingMatch } from './livePhase';

const match = (over: Partial<MyMatch>): MyMatch => ({
  matchId: 'm1', guildId: 'g', tournamentName: 'Cup', roundLabel: 'Round 1',
  lobbyState: 'host_lost', lobbyName: 'lobby', lobbyPassword: 'pw', hostName: null, isHost: true,
  gameMode: 'summoners_rift_5v5',
  ...over,
} as MyMatch);

const board: ChampSelectBoard = { timerPhase: 'BAN_PICK', members: [] };
const game = (status: 'in_game' | 'ended', gameMode = 'CLASSIC'): LiveGame => ({
  status, firstBlood: null, result: null, board: { gameTime: 42, gameMode, players: [] },
});

test('nothing on this machine → no local phase', () => {
  assert.equal(localPhase(null, null), null);
});

test('champ select board → starting; a running game → in_game; a finished one → result_pending', () => {
  assert.equal(localPhase(board, null), 'starting');
  assert.equal(localPhase(null, game('in_game')), 'in_game');
  assert.equal(localPhase(null, game('ended')), 'result_pending');
});

test('a running game beats a board the client has not dropped yet', () => {
  assert.equal(localPhase(board, game('in_game')), 'in_game');
});

test('a fresh champ select beats the frozen banner of the previous game', () => {
  assert.equal(localPhase(board, game('ended')), 'starting');
});

test('a by-hand lobby (host_lost) is carried to the phase the client shows', () => {
  const m = match({ lobbyState: 'host_lost' });
  const phase = localPhase(null, game('in_game'));
  const playing = pickPlayingMatch([m], phase, 'CLASSIC');
  assert.equal(playing?.matchId, 'm1');
  assert.equal(effectiveState(m, phase, playing), 'in_game');
  assert.equal(effectiveState(m, localPhase(board, null), pickPlayingMatch([m], 'starting', null)), 'starting');
  assert.equal(effectiveState(m, localPhase(null, game('ended')), pickPlayingMatch([m], 'result_pending', 'CLASSIC')), 'result_pending');
});

test('an automatic host stuck before its create ack (awaiting_create) is carried too', () => {
  const m = match({ lobbyState: 'awaiting_create' });
  assert.equal(effectiveState(m, 'in_game', pickPlayingMatch([m], 'in_game', 'CLASSIC')), 'in_game');
});

test('a match with no lobby yet, or one already over, is never the live game', () => {
  for (const lobbyState of [null, 'ended', 'aborted']) {
    assert.equal(pickPlayingMatch([match({ lobbyState })], 'in_game', 'CLASSIC'), undefined);
  }
});

test("the player's own ARAM does not light up a 5v5 card, and practice tool lights nothing", () => {
  const m = match({ gameMode: 'summoners_rift_5v5' });
  assert.equal(pickPlayingMatch([m], 'in_game', 'ARAM'), undefined);
  assert.equal(pickPlayingMatch([m], 'in_game', 'PRACTICETOOL'), undefined);
  assert.equal(effectiveState(m, 'in_game', undefined), 'host_lost');
});

test('an ARAM game fits the 1v1 duel; an unknown mode (or none yet) fits anything', () => {
  const duel = match({ gameMode: 'aram_1v1' });
  assert.equal(pickPlayingMatch([duel], 'in_game', 'ARAM')?.matchId, 'm1');
  assert.equal(pickPlayingMatch([duel], 'in_game', 'CLASSIC'), undefined);
  assert.equal(pickPlayingMatch([duel], 'starting', null)?.matchId, 'm1');
  assert.equal(pickPlayingMatch([duel], 'in_game', 'SOMETHINGNEW')?.matchId, 'm1');
});

test('two open matches: the one the backend knows is further along gets the game; the other keeps its state', () => {
  const a = match({ matchId: 'a', lobbyState: 'host_lost' });
  const b = match({ matchId: 'b', lobbyState: 'filling' });
  const playing = pickPlayingMatch([a, b], 'in_game', 'CLASSIC');
  assert.equal(playing?.matchId, 'b');
  assert.equal(effectiveState(b, 'in_game', playing), 'in_game');
  assert.equal(effectiveState(a, 'in_game', playing), 'host_lost');
});

test('the backend being further along wins over the local view', () => {
  const m = match({ lobbyState: 'result_pending' });
  assert.equal(effectiveState(m, 'in_game', pickPlayingMatch([m], 'in_game', 'CLASSIC')), 'result_pending');
});
