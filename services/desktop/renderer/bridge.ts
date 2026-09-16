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

export type UpdatePhase = 'idle' | 'checking' | 'up_to_date' | 'unknown' | 'available' | 'downloading' | 'ready' | 'installing' | 'failed';
/** Mirror of `UpdateState` in services/desktop/src/update/coordinator.ts. */
export interface UpdateState {
  current: string;
  platform: string;
  /** Can this build install the update itself (packaged + platform strategy)? */
  supported: boolean;
  phase: UpdatePhase;
  latest: string | null;
  /** Below the server's minimum: no "Later". */
  required: boolean;
  /** The manual installer link (always usable). */
  downloadUrl: string | null;
  /** 0..100 while downloading. */
  progress: number | null;
  error: string | null;
  lastCheckedAt: string | null;
}

export interface BridgeUser { userId: string; discordUserId: string; displayName: string }
export type SessionStatus = 'signed-in' | 'needs-pairing' | 'unavailable';
export interface SessionState { signedIn: boolean; user: BridgeUser | null; status?: SessionStatus }
/** The last summoner this device saw signed in to League (display only); what the UI falls back to while League is closed. */
export interface RememberedSummoner { ok: true; gameName?: string; tagLine?: string; region?: string; profileIconId?: number; summonerLevel?: number }
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

/** `guildId` is the row's own server: the lists span every server the user belongs to,
 *  and every per-object call (entrants, lock-in, bracket, hide) sends it back. */
export interface Tournament { id: string; guildId: string; name: string; status: string; format: string; gameMode: string; bestOf: number; maxTeams: number; isSharedToCentral: boolean; region: string | null; registrationOpensAt: string | null; startAt: string | null }
export interface MyTeam { teamId: string; name: string; role: string; status: string; guildId: string }
export interface TeamMember { teamId: string; userId: string; role: string; displayName: string }
export interface JoinRequest { teamId: string; teamName: string; userId: string; displayName: string; guildId: string }
/** One row of a live scoreboard — a display name, never a Riot ID. */
export interface LiveBoardPlayer { name: string; championKey: string; championName: string; team: string; level: number; isDead: boolean; respawnTimer: number; items: number[]; kills: number; deaths: number; assists: number; creepScore: number; wardScore: number; spell1Key: string | null; spell2Key: string | null }
export interface LiveBoard { matchId: string; gameTime: number; gameMode: string; updatedAt: string; players: LiveBoardPlayer[] }
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
  /** Live Solo/Duo rank for one linked account (re-read from Riot; optional on old builds). */
  refreshRank?(accountId: string): Promise<Res<{ account: LinkedAccount }>>;
  signOut(): Promise<{ ok: boolean }>;
  listMyTournaments(): Promise<Res<{ tournaments: Tournament[] }>>;
  listMyMatches(): Promise<Res<{ matches: MyMatch[] }>>;
  joinTournament(code: string, teamId?: string): Promise<Res<{ tournament: Tournament }>>;
  hideTournament(tournamentId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  tournamentBracket(tournamentId: string, guildId?: string): Promise<Res<{ tournament: Tournament; matches: BracketMatch[] }>>;
  lookupTournament(code: string): Promise<Res<{ tournament: Tournament }>>;
  tournamentEntrants(tournamentId: string, guildId?: string): Promise<Res<{ entrants: Entrant[] }>>;
  tournamentLockIn(tournamentId: string, teamId?: string, guildId?: string): Promise<Res<Record<string, never>>>;
  matchReady(matchId: string, guildId?: string): Promise<Res<{ side: string; bothReady: boolean }>>;
  createTeam(name: string): Promise<Res<{ team: { id: string; name: string; joinCode: string } }>>;
  joinTeam(code: string): Promise<Res<{ team: { id: string; name: string } }>>;
  teamMembers(teamId: string, guildId?: string): Promise<Res<{ members: TeamMember[] }>>;
  teamJoinCode(teamId: string, guildId?: string): Promise<Res<{ teamId: string; joinCode: string }>>;
  teamJoinRequests(): Promise<Res<{ requests: JoinRequest[] }>>;
  approveJoinRequest(teamId: string, userId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  rejectJoinRequest(teamId: string, userId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  removeMember(teamId: string, userId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  myTeams(): Promise<Res<{ teams: MyTeam[] }>>;
  leaveTeam(teamId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  deleteTeam(teamId: string, guildId?: string): Promise<Res<Record<string, never>>>;
  presence(): Promise<Res<{ playersInGame: number; entries: PresenceEntry[] }>>;
  /** The live scoreboard of a game in progress; absent on an old main-process build. */
  liveBoard?(matchId: string): Promise<Res<{ board: LiveBoard }>>;
  /** The signed-in player's recent games; absent on an old main-process build. */
  myHistory?(): Promise<Res<{ games: HistoryGame[] }>>;
  /** The last summoner icon/level this device saw (works with League closed); absent on an old build. */
  getProfile?(): Promise<RememberedSummoner>;
  /** Self-update state (current version, latest, phase, progress); absent on an old build. */
  getUpdateState?(): Promise<UpdateState>;
  /** "Check for updates": asks the backend now and resolves with the new state. */
  checkForUpdates?(): Promise<UpdateState>;
  /** Download (if needed) and install the update; the app restarts itself. */
  installUpdate?(): Promise<UpdateState>;
  /** Subscribe to update-state changes. Returns an unsubscribe function. */
  onUpdate?(callback: (state: UpdateState) => void): () => void;
  /** Open an http(s) link in the system browser. */
  openExternal?(url: string): Promise<{ ok: true } | { ok: false; detail: string }>;
  /** Is this client allowed to drive the League client (lobby writes)? */
  getAutomation(): Promise<{ ok: true; enabled: boolean }>;
  /** Grant/revoke that permission; remembered per device (settings.json). */
  setAutomation(enabled: boolean): Promise<{ ok: true; enabled: boolean }>;
  readSummoner(): Promise<ReadSummoner>;
  onLcuStatus(cb: (s: LcuStatus) => void): () => void;
  getLcuStatus?(): Promise<LcuStatus | null>;
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
    // Pull as well as listen: pushes only come on a change, so a badge that
    // mounts after the last one (League already open) would never hear it. A
    // push that beats the pull is newer, so the pull must not overwrite it.
    let pushed = false;
    const off = b.onLcuStatus((s) => { pushed = true; setStatus(s); });
    b.getLcuStatus?.().then((s) => { if (s !== null && !pushed) setStatus(s); }, () => undefined);
    return off;
  }, []);
  return status;
}

/** The self-update state, live; null on a build without the updater bridge. */
export function useUpdateState(): UpdateState | null {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    const b = lol();
    if (!b || typeof b.onUpdate !== 'function') return;
    let alive = true;
    if (typeof b.getUpdateState === 'function') void b.getUpdateState().then((s) => { if (alive) setState(s); });
    const off = b.onUpdate((s) => { if (alive) setState(s); });
    return () => { alive = false; off(); };
  }, []);
  return state;
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
