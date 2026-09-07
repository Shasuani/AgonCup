// Small display helpers shared by the player-facing screens.
import { useEffect, useState } from 'react';
import type { HistoryGame, HistoryPlayer, LinkedAccount } from './bridge';

/** Re-renders every `ms` so countdowns and "x min ago" stay honest. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** "3 min ago", "2 h ago", "4 d ago" — for the recent-games strip. */
export function timeAgo(iso: string, now = Date.now()): string {
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return '';
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d} d ago`;
  return new Date(iso).toLocaleDateString();
}

/** "2d 4h", "1h 12m", "45s" until `iso`; null once it has passed. */
export function countdown(iso: string, now = Date.now()): string | null {
  const at = new Date(iso).getTime();
  if (Number.isNaN(at) || at <= now) return null;
  const s = Math.floor((at - now) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${s}s`;
}

/** Short local date-time ("7 Sep, 21:00"). */
export function shortWhen(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function kdaRatio(p: { kills: number; deaths: number; assists: number }): string {
  const r = (p.kills + p.assists) / Math.max(1, p.deaths);
  return p.deaths === 0 ? 'Perfect' : `${r.toFixed(1)} KDA`;
}

export function playerLabel(p: HistoryPlayer): string {
  return p.displayName ?? p.riotId ?? (p.puuid ? `${p.puuid.slice(0, 8)}…` : 'Unknown');
}

/** The Riot IDs ("Name#TAG") of the signed-in player's linked accounts. */
export function myRiotIds(accounts: LinkedAccount[]): Set<string> {
  return new Set(accounts.filter((a) => a.gameName && a.tagLine).map((a) => `${a.gameName}#${a.tagLine}`.toLowerCase()));
}

/** Find the caller's own line in a game: by linked Riot ID, else by Discord name. */
export function findMe(game: HistoryGame, riotIds: Set<string>, displayName: string | null): { team: number; player: HistoryPlayer } | null {
  for (let ti = 0; ti < game.teams.length; ti += 1) {
    for (const p of game.teams[ti]!.players) {
      if (p.riotId && riotIds.has(p.riotId.toLowerCase())) return { team: ti, player: p };
    }
  }
  if (displayName) {
    for (let ti = 0; ti < game.teams.length; ti += 1) {
      for (const p of game.teams[ti]!.players) {
        if (p.displayName && p.displayName.toLowerCase() === displayName.toLowerCase()) return { team: ti, player: p };
      }
    }
  }
  return null;
}

export interface Record { games: number; wins: number; kills: number; deaths: number; assists: number; champions: Map<number, number> }

/** Aggregate the caller's record across their games. */
export function myRecord(games: HistoryGame[], riotIds: Set<string>, displayName: string | null): Record {
  const rec: Record = { games: 0, wins: 0, kills: 0, deaths: 0, assists: 0, champions: new Map() };
  for (const g of games) {
    const me = findMe(g, riotIds, displayName);
    if (!me) continue;
    rec.games += 1;
    if (g.teams[me.team]?.isWinner) rec.wins += 1;
    rec.kills += me.player.kills; rec.deaths += me.player.deaths; rec.assists += me.player.assists;
    rec.champions.set(me.player.championId, (rec.champions.get(me.player.championId) ?? 0) + 1);
  }
  return rec;
}

export function topChampion(rec: Record): number | null {
  let best: number | null = null; let n = 0;
  for (const [id, count] of rec.champions) if (count > n && id > 0) { best = id; n = count; }
  return best;
}
