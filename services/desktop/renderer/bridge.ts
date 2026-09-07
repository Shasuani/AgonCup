// Typed accessor + React hooks over the `window.lol` IPC bridge exposed by
// services/desktop/src/preload.cts. These mirror the bridge's result shapes;
// keep in sync with preload.cts.
import { useCallback, useEffect, useRef, useState } from 'react';

export type LcuStatus = 'online' | 'loading' | 'offline';

/** One champ-select seat (display labels only — no identity fields). */
export interface ChampSelectMember {
  cellId: number;
  side: 'my' | 'their';
  gameName?: string;
  championId: number;
  championName?: string;
  locked: boolean;
}
/** The live champ-select board; null while the client is not in champ select. */
export interface ChampSelectBoard { timerPhase?: string; members: ChampSelectMember[] }

/** First blood on the live-game banner (decides a 1v1 ARAM duel). */
export interface LiveGameFirstBlood {
  scorer: string;
  /** Whether the scorer is THIS player; null when the local name is unknown. */
  isYou: boolean | null;
  atSeconds: number;
}
/** The running (or just-finished) game; null while there is nothing to show. */
export interface LiveGame {
  status: 'in_game' | 'ended';
  firstBlood: LiveGameFirstBlood | null;
  /** The LOCAL player's outcome from the GameEnd event; null until known. */
  result: 'win' | 'loss' | null;
}

export interface BridgeUser { userId: string; discordUserId: string; displayName: string }
export type SessionStatus = 'signed-in' | 'needs-pairing' | 'unavailable';
export interface SessionState { signedIn: boolean; user: BridgeUser | null; status?: SessionStatus }
export interface ReadSummoner { ok: boolean; puuid?: string; gameName?: string; tagLine?: string; region?: string; profileIconId?: number; summonerLevel?: number; reason?: string; detail?: string }
export interface LinkedAccount { accountId: string; gameName: string | null; tagLine: string | null; region: string | null; verified: boolean; rankText: string }
/** A live match of the caller's, with whatever is known about its lobby. */
export interface MyMatch {
  matchId: string;
  guildId: string;
  tournamentName: string;
  roundLabel: string;
  /** Null until a lobby is opened; otherwise the lobby state-machine phase. */
  lobbyState: string | null;
  lobbyName: string | null;
  lobbyPassword: string | null;
  /** Null when nobody's client is hosting — the player creates it themselves. */
  hostName: string | null;
  isHost: boolean;
  /** GameMode value ('summoners_rift_5v5' / 'aram_1v1') — mode line + win rule. */
  gameMode: string;
}

export interface Tournament { id: string; name: string; status: string; format: string; gameMode: string; bestOf: number; maxTeams: number; isSharedToCentral: boolean; region: string | null; registrationOpensAt: string | null; startAt: string | null }
export interface MyTeam { teamId: string; name: string; role: string; status: string }
export interface TeamMember { teamId: string; userId: string; role: string; displayName: string }
export interface JoinRequest { teamId: string; teamName: string; userId: string; displayName: string }
export interface PresenceEntry { matchId: string; guildName: string; tournamentName: string; gameMode: string; startedAt: string | null; players: string[] }
export interface BracketMatch { id: string; round: number; slot: number; teamAName: string | null; teamBName: string | null; winnerTeamId: string | null; teamAId: string | null; teamBId: string | null; status: string; readyDeadlineAt: string | null; teamAReady: boolean; teamBReady: boolean }

/** One scoreboard line of a played game (display-only, rebuilt from eog reports). */
export interface HistoryPlayer { puuid: string; displayName: string | null; riotId: string | null; championId: number; kills: number; deaths: number; assists: number; championName: string | null; level: number | null; items: number[]; spell1Id: number | null; spell2Id: number | null; goldEarned: number | null; minionsKilled: number | null; damageToChampions: number | null; visionScore: number | null; profileIconId: number | null }
export interface HistoryTeam { isWinner: boolean; players: HistoryPlayer[] }
export interface HistoryGame { matchId: string; tournamentId: string; tournamentName: string | null; round: number | null; lcuGameId: string; playedAt: string; teams: HistoryTeam[] }

export interface EntrantPlayer { userId: string; name: string; lockedIn: boolean; isYou: boolean }
export interface Entrant { teamId: string; name: string; lockedIn: boolean; isSolo: boolean; isYou: boolean; canLockIn: boolean; players: EntrantPlayer[] }

type Ok<T> = ({ ok: true } & T);
type Err = { ok: false; status?: number; detail?: string };
type Res<T> = Ok<T> | Err;

export interface LolBridge {
  getSession(): Promise<SessionState>;
  pair(code: string, deviceLabel?: string): Promise<{ ok: boolean; status?: number; detail?: string; user?: BridgeUser }>;
  refresh(): Promise<{ ok: boolean }>;
  verify(): Promise<{ ok: boolean; status?: number; detail?: string; rankText?: string; gameName?: string; tagLine?: string }>;
  listAccounts(): Promise<Res<{ accounts: LinkedAccount[] }>>;
  unlinkAccount(accountId: string): Promise<Res<{ account: LinkedAccount }>>;
  signOut(): Promise<{ ok: boolean }>;
  listMyTournaments(): Promise<Res<{ tournaments: Tournament[] }>>;
  listMyMatches(): Promise<Res<{ matches: MyMatch[] }>>;
  joinTournament(code: string, teamId?: string): Promise<Res<{ tournament: Tournament }>>;
  hideTournament(tournamentId: string): Promise<Res<Record<string, never>>>;
  tournamentBracket(tournamentId: string): Promise<Res<{ tournament: Tournament; matches: BracketMatch[] }>>;
  lookupTournament(code: string): Promise<Res<{ tournament: Tournament }>>;
  tournamentEntrants(tournamentId: string): Promise<Res<{ entrants: Entrant[] }>>;
  tournamentLockIn(tournamentId: string, teamId?: string): Promise<Res<Record<string, never>>>;
  matchReady(matchId: string): Promise<Res<{ side: string; bothReady: boolean }>>;
  createTeam(name: string): Promise<Res<{ team: { id: string; name: string; joinCode: string } }>>;
  joinTeam(code: string): Promise<Res<{ team: { id: string; name: string } }>>;
  teamMembers(teamId: string): Promise<Res<{ members: TeamMember[] }>>;
  teamJoinCode(teamId: string): Promise<Res<{ teamId: string; joinCode: string }>>;
  teamJoinRequests(): Promise<Res<{ requests: JoinRequest[] }>>;
  approveJoinRequest(teamId: string, userId: string): Promise<Res<Record<string, never>>>;
  rejectJoinRequest(teamId: string, userId: string): Promise<Res<Record<string, never>>>;
  removeMember(teamId: string, userId: string): Promise<Res<Record<string, never>>>;
  myTeams(): Promise<Res<{ teams: MyTeam[] }>>;
  leaveTeam(teamId: string): Promise<Res<Record<string, never>>>;
  deleteTeam(teamId: string): Promise<Res<Record<string, never>>>;
  presence(): Promise<Res<{ playersInGame: number; entries: PresenceEntry[] }>>;
  /** The signed-in player's recent games; absent on an old main-process build. */
  myHistory?(): Promise<Res<{ games: HistoryGame[] }>>;
  /** The last summoner icon/level this device saw (works with League closed); absent on an old build. */
  getProfile?(): Promise<{ ok: true; profileIconId?: number; summonerLevel?: number }>;
  /** Is this client allowed to drive the League client (lobby writes)? */
  getAutomation(): Promise<{ ok: true; enabled: boolean }>;
  /** Grant/revoke that permission; remembered per device (settings.json). */
  setAutomation(enabled: boolean): Promise<{ ok: true; enabled: boolean }>;
  readSummoner(): Promise<ReadSummoner>;
  onLcuStatus(cb: (s: LcuStatus) => void): () => void;
  onChampSelect?(cb: (board: ChampSelectBoard | null) => void): () => void;
  onLiveGame?(cb: (view: LiveGame | null) => void): () => void;
  getLiveGame?(): Promise<LiveGame | null>;
  onAuthLost(cb: () => void): () => void;
  onSessionRestored(cb: () => void): () => void;
}

declare global {
  interface Window { lol?: LolBridge }
}

/** The bridge, or null when running outside Electron (e.g. plain browser preview). */
export function lol(): LolBridge | null {
  return typeof window !== 'undefined' && window.lol ? window.lol : null;
}

/** Run an async bridge call on mount (and when deps change); expose {data,loading,error,reload}.
 *  Pass pollMs to background-refresh on an interval WITHOUT flashing the spinner
 *  (loading stays false once the first load lands) — used for live bracket updates. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number): { data: T | null; loading: boolean; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  const loadedOnce = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!loadedOnce.current) setLoading(true); // spinner only before the first result
    fn().then(
      (d) => { if (alive.current) { setData(d); setError(null); setLoading(false); loadedOnce.current = true; } },
      (e) => { if (alive.current) { setError(String(e?.message ?? e)); setLoading(false); loadedOnce.current = true; } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => setTick((n) => n + 1), pollMs);
    return () => clearInterval(id);
  }, [pollMs]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, reload };
}

/** Live LCU reachability badge state. */
export function useLcuStatus(): LcuStatus {
  const [status, setStatus] = useState<LcuStatus>('loading');
  useEffect(() => {
    const b = lol();
    if (!b) return;
    return b.onLcuStatus(setStatus);
  }, []);
  return status;
}

/** Live champ-select board; null while not in champ select (or an old build). */
export function useChampSelect(): ChampSelectBoard | null {
  const [board, setBoard] = useState<ChampSelectBoard | null>(null);
  useEffect(() => {
    const b = lol();
    if (!b || typeof b.onChampSelect !== 'function') return;
    return b.onChampSelect(setBoard);
  }, []);
  return board;
}

/** The live-game winner banner: pull once (the banner outlives the game, so the
 *  last push may predate this mount), then follow pushes. Null = nothing to show
 *  (or an old build without the channel). */
export function useLiveGame(): LiveGame | null {
  const [view, setView] = useState<LiveGame | null>(null);
  useEffect(() => {
    const b = lol();
    if (!b || typeof b.onLiveGame !== 'function') return;
    if (typeof b.getLiveGame === 'function') {
      b.getLiveGame().then(setView, () => undefined);
    }
    return b.onLiveGame(setView);
  }, []);
  return view;
}

/** Narrow a bridge Res<T> to its detail string on failure. */
export function errText(r: { ok: false; status?: number; detail?: string }): string {
  return r.detail ?? `Request failed${r.status ? ` (${r.status})` : ''}`;
}
