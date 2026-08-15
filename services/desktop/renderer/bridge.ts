// Typed accessor + React hooks over the `window.lol` IPC bridge exposed by
// services/desktop/src/preload.cts. These mirror the bridge's result shapes;
// keep in sync with preload.cts.
import { useCallback, useEffect, useRef, useState } from 'react';

export type LcuStatus = 'online' | 'loading' | 'offline';

export interface BridgeUser { userId: string; discordUserId: string; displayName: string }
export type SessionStatus = 'signed-in' | 'needs-pairing' | 'unavailable';
export interface SessionState { signedIn: boolean; user: BridgeUser | null; status?: SessionStatus }
export interface ReadSummoner { ok: boolean; puuid?: string; gameName?: string; tagLine?: string; region?: string; reason?: string; detail?: string }
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
}

export interface Tournament { id: string; name: string; status: string; format: string; gameMode: string; bestOf: number; maxTeams: number; isSharedToCentral: boolean; registrationOpensAt: string | null; startAt: string | null }
export interface MyTeam { teamId: string; name: string; role: string; status: string }
export interface TeamMember { teamId: string; userId: string; role: string; displayName: string }
export interface JoinRequest { teamId: string; teamName: string; userId: string; displayName: string }
export interface PresenceEntry { matchId: string; guildName: string; tournamentName: string; gameMode: string; startedAt: string | null; players: string[] }
export interface BracketMatch { id: string; round: number; slot: number; teamAName: string | null; teamBName: string | null; winnerTeamId: string | null; teamAId: string | null; teamBId: string | null; status: string; readyDeadlineAt: string | null; teamAReady: boolean; teamBReady: boolean }

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
  readSummoner(): Promise<ReadSummoner>;
  onLcuStatus(cb: (s: LcuStatus) => void): () => void;
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

/** Narrow a bridge Res<T> to its detail string on failure. */
export function errText(r: { ok: false; status?: number; detail?: string }): string {
  return r.detail ?? `Request failed${r.status ? ` (${r.status})` : ''}`;
}
