import React, { useEffect, useState } from 'react';
import { ChampIcon, ItemRow, SpellPair, championIdByKey, spellIdByKey, useChampions } from './champion';
import type { LiveBoardPlayer } from './bridge';

/**
 * The live scoreboard — what every player sees on TAB: champion, level, spells,
 * items, K/D/A and CS per player, blue side against red side. Shared by Watch
 * live (a spectator's view of someone else's game, via the backend) and Live
 * match (this machine's own game, straight from `:2999`). Display-only, never
 * the result.
 */
export type ScoreboardPlayer = LiveBoardPlayer & { isYou?: boolean | null };

export function Scoreboard({ players }: { players: ScoreboardPlayer[] }) {
  const blue = players.filter((p) => p.team !== 'CHAOS');
  const red = players.filter((p) => p.team === 'CHAOS');
  return (
    <div className="ll-board">
      <Side title="Blue side" tone="#5aa8ff" players={blue} />
      <Side title="Red side" tone="#ff6b6b" players={red} />
    </div>
  );
}

/** Kills of one side, for the "12 vs 9" headline. */
export function teamKills(players: ScoreboardPlayer[], side: 'blue' | 'red'): number {
  return players
    .filter((p) => (p.team === 'CHAOS') === (side === 'red'))
    .reduce((n, p) => n + p.kills, 0);
}

function Side({ title, tone, players }: { title: string; tone: string; players: ScoreboardPlayer[] }) {
  return (
    <div className="ll-board__team">
      <div className="ll-board__head">
        <span className="ll-cap" style={{ color: tone }}>{title}</span>
      </div>
      {players.map((p, i) => <Row key={`${p.championKey}:${i}`} p={p} />)}
      {players.length === 0 && <div className="ll-dim" style={{ fontSize: 12 }}>—</div>}
    </div>
  );
}

function Row({ p }: { p: ScoreboardPlayer }) {
  const reg = useChampions();
  const me = p.isYou === true;
  return (
    <div className={`ll-board__row${me ? ' ll-board__row--me' : ''}`} style={p.isDead ? { opacity: 0.55 } : undefined} data-live-player={p.name}>
      <ChampIcon id={championIdByKey(reg, p.championKey)} name={p.championName || undefined} size={30} ring="none" level={p.level} />
      <SpellPair a={spellIdByKey(reg, p.spell1Key)} b={spellIdByKey(reg, p.spell2Key)} size={14} />
      <span className="ll-board__name" title={p.championName}>
        {p.name}
        {me && <span className="ll-you"> · you</span>}
        {p.isDead && <span style={{ color: '#ff6b6b', marginLeft: 6, fontSize: 11.5 }}>dead · {Math.ceil(p.respawnTimer)}s</span>}
      </span>
      <ItemRow items={p.items} size={20} />
      <span className="ll-board__kda">
        {p.kills} / {p.deaths} / {p.assists}
        <small>{p.creepScore} CS</small>
      </span>
    </div>
  );
}

/** The game clock, ticking locally between boards so it never looks frozen. */
export function useGameClock(board: { gameTime: number } | null | undefined, key?: string, frozen = false): string {
  const [now, setNow] = useState(() => Date.now());
  const [anchor, setAnchor] = useState<{ gameTime: number; at: number } | null>(null);
  useEffect(() => {
    if (board) setAnchor({ gameTime: board.gameTime, at: Date.now() });
  }, [board?.gameTime, key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!anchor) return '--:--';
  // Never run more than one poll window ahead of the last board we actually saw;
  // a finished game stops on its final time.
  const seconds = anchor.gameTime + (frozen ? 0 : Math.min((now - anchor.at) / 1000, 10));
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
