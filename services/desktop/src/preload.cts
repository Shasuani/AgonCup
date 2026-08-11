/**
 * Electron preload — the ONLY bridge between the sandboxed renderer and the
 * privileged main process. The renderer runs with `contextIsolation:true` +
 * `nodeIntegration:false`, so it has no Node / network access; it asks the main
 * process to read the local LCU, pair, refresh, and link the account via these
 * narrow IPC channels.
 *
 * Exposes `window.lol`:
 *   - `getSession()`           -> current sign-in state (pairing vs verify view).
 *   - `pair(code, label?)`     -> exchange a pairing code for a session.
 *   - `refresh()`              -> silent session refresh.
 *   - `verify()`              -> authenticated step-2 LCU account link (no code).
 *   - `readSummoner()`         -> main reads the LCU current summoner (READ-ONLY).
 *   - `onLcuStatus(cb)`        -> subscribe to LCU reachability badge updates.
 *   - `onAuthLost(cb)`         -> subscribe to "session expired, re-pair" events.
 *   - `onSessionRestored(cb)` -> subscribe to "backend is back, session restored".
 *
 * Nothing privileged is leaked: only these narrow, validated functions are
 * placed on `window`.
 */

// CommonJS import form (this is a .cts file under verbatimModuleSyntax).
import electron = require("electron");
const { contextBridge, ipcRenderer } = electron;

/** Result of `window.lol.readSummoner()`. */
export interface ReadSummonerBridgeResult {
  ok: boolean;
  puuid?: string;
  summonerName?: string;
  /** Riot ID game name (reliable; displayName is empty on modern clients). */
  gameName?: string;
  /** Riot ID tag line (reliable). */
  tagLine?: string;
  summonerId?: string;
  region?: string;
  reason?: string;
  detail?: string;
}

/** Minimal user summary for the signed-in UI. */
export interface BridgeUser {
  userId: string;
  discordUserId: string;
  displayName: string;
}

/** Linked-account summary (present when the user has already linked Riot). */
export interface BridgeAccount {
  linked: boolean;
  puuid: string | null;
  gameName: string | null;
  tagLine: string | null;
  region: string | null;
  verified: boolean;
  rankText: string;
}

/** Result of `window.lol.getSession()`. */
export interface SessionStateBridgeResult {
  signedIn: boolean;
  user: BridgeUser | null;
  /** How to render being signed out. "needs-pairing" = no credential, ask for a
   * code. "unavailable" = we HOLD one and the backend is unreachable; the app is
   * retrying and must NOT ask for a code. */
  status?: "signed-in" | "needs-pairing" | "unavailable";
}

/** Result of `window.lol.pair(...)`. */
export interface PairBridgeResult {
  ok: boolean;
  status?: number;
  detail?: string;
  user?: BridgeUser;
  account?: BridgeAccount | null;
}

/** Result of `window.lol.verify()`. */
export interface VerifyBridgeResult {
  ok: boolean;
  accountId?: string;
  puuid?: string;
  status?: number;
  detail?: string;
  /** Solo/Duo rank snapshot from the backend (best-effort; may be absent). */
  tier?: string;
  division?: string;
  leaguePoints?: number;
  rankText?: string;
  /** Canonical Riot ID of the linked account (for "linked X#Y" copy). */
  gameName?: string;
  tagLine?: string;
}

/** One linked account in `window.lol.listAccounts()`. */
export interface LinkedAccountBridge {
  accountId: string;
  gameName: string | null;
  tagLine: string | null;
  region: string | null;
  verified: boolean;
  rankText: string;
}

/** Result of `window.lol.listAccounts()`. */
export type ListAccountsBridgeResult =
  | { ok: true; accounts: LinkedAccountBridge[] }
  | { ok: false; status: number; detail: string };

/** Result of `window.lol.unlinkAccount(id)`. */
export type UnlinkAccountBridgeResult =
  | { ok: true; account: LinkedAccountBridge }
  | { ok: false; status: number; detail: string };

/** One tournament in the signed-in user's list / a join result. */
export interface TournamentBridge {
  id: string;
  name: string;
  status: string;
  format: string;
  gameMode: string;
  bestOf: number;
  maxTeams: number;
  isSharedToCentral: boolean;
  registrationOpensAt: string | null;
  startAt: string | null;
}

export type ListTournamentsBridgeResult =
  | { ok: true; tournaments: TournamentBridge[] }
  | { ok: false; status: number; detail: string };

export type JoinTournamentBridgeResult =
  | { ok: true; tournament: TournamentBridge }
  | { ok: false; status: number; detail: string };

export type HideTournamentBridgeResult =
  | { ok: true }
  | { ok: false; status: number; detail: string };

/** One bracket match (team names resolved). */
export interface BracketMatchBridge {
  id: string;
  round: number;
  slot: number;
  teamAName: string | null;
  teamBName: string | null;
  winnerTeamId: string | null;
  teamAId: string | null;
  teamBId: string | null;
  status: string;
  readyDeadlineAt: string | null;
  teamAReady: boolean;
  teamBReady: boolean;
}

export type BracketBridgeResult =
  | { ok: true; tournament: TournamentBridge; matches: BracketMatchBridge[] }
  | { ok: false; status: number; detail: string };

export type ReadyBridgeResult =
  | { ok: true; side: string; bothReady: boolean }
  | { ok: false; status: number; detail: string };

/** A roster member of an entry, with their own lock-in state. */
export interface EntrantPlayerBridge {
  userId: string;
  name: string;
  lockedIn: boolean;
  isYou: boolean;
}

/** One tournament entry: a team in 5v5, a player (their solo team) in 1v1. */
export interface EntrantBridge {
  teamId: string;
  name: string;
  lockedIn: boolean;
  isSolo: boolean;
  isYou: boolean;
  canLockIn: boolean;
  players: EntrantPlayerBridge[];
}

export type EntrantsBridgeResult =
  | { ok: true; entrants: EntrantBridge[] }
  | { ok: false; status: number; detail: string };

export type LockInBridgeResult =
  | { ok: true }
  | { ok: false; status: number; detail: string };

/** A team as `createTeam` returns it (includes the shareable join code). */
export interface CreatedTeamBridge {
  id: string;
  name: string;
  captainUserId: string;
  guildId: string;
  joinCode: string;
}

/** A team as `joinTeamByCode` returns it (no join code echoed to joiners). */
export interface JoinedTeamBridge {
  id: string;
  name: string;
  captainUserId: string;
  guildId: string;
}

/** One roster row. */
export interface TeamMemberBridge {
  teamId: string;
  userId: string;
  role: string;
  displayName: string;
}

export type CreateTeamBridgeResult =
  | { ok: true; team: CreatedTeamBridge }
  | { ok: false; status: number; detail: string };

export type JoinTeamBridgeResult =
  | { ok: true; team: JoinedTeamBridge }
  | { ok: false; status: number; detail: string };

export type TeamMembersBridgeResult =
  | { ok: true; members: TeamMemberBridge[] }
  | { ok: false; status: number; detail: string };

export type TeamJoinCodeBridgeResult =
  | { ok: true; teamId: string; joinCode: string }
  | { ok: false; status: number; detail: string };

/** One pending join request surfaced to the captain (with the joiner's name). */
export interface JoinRequestBridge {
  teamId: string;
  teamName: string;
  userId: string;
  displayName: string;
}

export type TeamJoinRequestsBridgeResult =
  | { ok: true; requests: JoinRequestBridge[] }
  | { ok: false; status: number; detail: string };

export type ActOnRequestBridgeResult =
  | { ok: true }
  | { ok: false; status: number; detail: string };

/** A team the signed-in user belongs to, with their role + membership status. */
export interface MyTeamBridge {
  teamId: string;
  name: string;
  role: string;
  status: string;
}

export type MyTeamsBridgeResult =
  | { ok: true; teams: MyTeamBridge[] }
  | { ok: false; status: number; detail: string };

/** One live match in the presence panel. */
export interface PresenceEntryBridge {
  matchId: string;
  guildName: string;
  tournamentName: string;
  gameMode: string;
  startedAt: string | null;
  players: string[];
}

export type PresenceBridgeResult =
  | { ok: true; playersInGame: number; entries: PresenceEntryBridge[] }
  | { ok: false; status: number; detail: string };

/** LCU reachability badge state pushed from the main-process poller. */
export type LcuStatusBridge = "online" | "loading" | "offline";

/** Public API surface exposed on `window.lol`. */
export interface LolBridge {
  getSession(): Promise<SessionStateBridgeResult>;
  pair(code: string, deviceLabel?: string): Promise<PairBridgeResult>;
  refresh(): Promise<{ ok: boolean }>;
  verify(): Promise<VerifyBridgeResult>;
  /** List the signed-in user's own linked Riot accounts. */
  listAccounts(): Promise<ListAccountsBridgeResult>;
  /** Unregister one linked Riot account. */
  unlinkAccount(accountId: string): Promise<UnlinkAccountBridgeResult>;
  /** Clear the local session (sign out) → next boot / view shows pairing (step 1). */
  signOut(): Promise<{ ok: boolean }>;
  /** The signed-in user's tournaments. */
  listMyTournaments(): Promise<ListTournamentsBridgeResult>;
  /** Join a tournament by its code. `teamId` picks the captained team for 5v5. */
  joinTournament(code: string, teamId?: string): Promise<JoinTournamentBridgeResult>;
  /** Clear a FINISHED tournament off this user's own list (per-user, not a delete). */
  hideTournament(tournamentId: string): Promise<HideTournamentBridgeResult>;
  /** The tournament's bracket (matches with team names). */
  tournamentBracket(tournamentId: string): Promise<BracketBridgeResult>;
  /** Look up a join code without enrolling (to learn the game mode first). */
  lookupTournament(code: string): Promise<JoinTournamentBridgeResult>;
  /** Who has entered a tournament, and who has locked in. */
  tournamentEntrants(tournamentId: string): Promise<EntrantsBridgeResult>;
  /** Lock in for a tournament. `teamId` only when captaining several teams. */
  tournamentLockIn(tournamentId: string, teamId?: string): Promise<LockInBridgeResult>;
  /** Mark the caller's team ready for a match (team captain). */
  matchReady(matchId: string): Promise<ReadyBridgeResult>;
  /** Create a team (acting user becomes captain; response carries the join code). */
  createTeam(name: string): Promise<CreateTeamBridgeResult>;
  /** Join a team by its code. */
  joinTeam(code: string): Promise<JoinTeamBridgeResult>;
  /** A team's roster. */
  teamMembers(teamId: string): Promise<TeamMembersBridgeResult>;
  /** A team's join code (captain-only). */
  teamJoinCode(teamId: string): Promise<TeamJoinCodeBridgeResult>;
  /** Pending join requests across every team the signed-in user captains. */
  teamJoinRequests(): Promise<TeamJoinRequestsBridgeResult>;
  /** Approve a pending join request (captain of that team). */
  approveJoinRequest(teamId: string, userId: string): Promise<ActOnRequestBridgeResult>;
  /** Reject a pending join request (captain of that team). */
  rejectJoinRequest(teamId: string, userId: string): Promise<ActOnRequestBridgeResult>;
  /** Remove a member from a team (captain of that team). */
  removeMember(teamId: string, userId: string): Promise<ActOnRequestBridgeResult>;
  /** Teams the signed-in user belongs to (with their role + status). */
  myTeams(): Promise<MyTeamsBridgeResult>;
  /** Leave a team (self-service; captains must transfer/disband first). */
  leaveTeam(teamId: string): Promise<ActOnRequestBridgeResult>;
  /** Disband a team you captain (deletes the team + its roster). */
  deleteTeam(teamId: string): Promise<ActOnRequestBridgeResult>;
  /** Global live presence (who is in a game + which server). */
  presence(): Promise<PresenceBridgeResult>;
  readSummoner(): Promise<ReadSummonerBridgeResult>;
  /** Subscribe to LCU badge updates. Returns an unsubscribe function. */
  onLcuStatus(callback: (status: LcuStatusBridge) => void): () => void;
  /** Subscribe to "session expired" events. Returns an unsubscribe function. */
  onAuthLost(callback: () => void): () => void;
  /** Subscribe to "a background retry got the session back" events, so a screen
   * showing "reconnecting…" can leave it. Returns an unsubscribe function. */
  onSessionRestored(callback: () => void): () => void;
}

const bridge: LolBridge = {
  getSession: () => ipcRenderer.invoke("lol:session-state") as Promise<SessionStateBridgeResult>,
  pair: (code, deviceLabel) =>
    ipcRenderer.invoke("lol:pair", { code, deviceLabel }) as Promise<PairBridgeResult>,
  refresh: () => ipcRenderer.invoke("lol:refresh") as Promise<{ ok: boolean }>,
  verify: () => ipcRenderer.invoke("lol:verify") as Promise<VerifyBridgeResult>,
  listAccounts: () =>
    ipcRenderer.invoke("lol:list-accounts") as Promise<ListAccountsBridgeResult>,
  unlinkAccount: (accountId) =>
    ipcRenderer.invoke("lol:account-unlink", { accountId }) as Promise<UnlinkAccountBridgeResult>,
  signOut: () => ipcRenderer.invoke("lol:sign-out") as Promise<{ ok: boolean }>,
  listMyTournaments: () =>
    ipcRenderer.invoke("lol:tournaments-mine") as Promise<ListTournamentsBridgeResult>,
  joinTournament: (code, teamId) =>
    ipcRenderer.invoke("lol:tournament-join", { code, teamId }) as Promise<JoinTournamentBridgeResult>,
  hideTournament: (tournamentId) =>
    ipcRenderer.invoke("lol:tournament-hide", { tournamentId }) as Promise<HideTournamentBridgeResult>,
  tournamentBracket: (tournamentId) =>
    ipcRenderer.invoke("lol:tournament-bracket", { tournamentId }) as Promise<BracketBridgeResult>,
  lookupTournament: (code) =>
    ipcRenderer.invoke("lol:tournament-lookup", { code }) as Promise<JoinTournamentBridgeResult>,
  tournamentEntrants: (tournamentId) =>
    ipcRenderer.invoke("lol:tournament-entrants", { tournamentId }) as Promise<EntrantsBridgeResult>,
  tournamentLockIn: (tournamentId, teamId) =>
    ipcRenderer.invoke("lol:tournament-lock-in", {
      tournamentId,
      teamId,
    }) as Promise<LockInBridgeResult>,
  matchReady: (matchId) =>
    ipcRenderer.invoke("lol:match-ready", { matchId }) as Promise<ReadyBridgeResult>,
  createTeam: (name) =>
    ipcRenderer.invoke("lol:team-create", { name }) as Promise<CreateTeamBridgeResult>,
  joinTeam: (code) =>
    ipcRenderer.invoke("lol:team-join", { code }) as Promise<JoinTeamBridgeResult>,
  teamMembers: (teamId) =>
    ipcRenderer.invoke("lol:team-members", { teamId }) as Promise<TeamMembersBridgeResult>,
  teamJoinCode: (teamId) =>
    ipcRenderer.invoke("lol:team-code", { teamId }) as Promise<TeamJoinCodeBridgeResult>,
  teamJoinRequests: () =>
    ipcRenderer.invoke("lol:team-join-requests") as Promise<TeamJoinRequestsBridgeResult>,
  approveJoinRequest: (teamId, userId) =>
    ipcRenderer.invoke("lol:team-approve", { teamId, userId }) as Promise<ActOnRequestBridgeResult>,
  rejectJoinRequest: (teamId, userId) =>
    ipcRenderer.invoke("lol:team-reject", { teamId, userId }) as Promise<ActOnRequestBridgeResult>,
  removeMember: (teamId, userId) =>
    ipcRenderer.invoke("lol:team-remove-member", { teamId, userId }) as Promise<ActOnRequestBridgeResult>,
  myTeams: () => ipcRenderer.invoke("lol:teams-mine") as Promise<MyTeamsBridgeResult>,
  leaveTeam: (teamId) =>
    ipcRenderer.invoke("lol:team-leave", { teamId }) as Promise<ActOnRequestBridgeResult>,
  deleteTeam: (teamId) =>
    ipcRenderer.invoke("lol:team-delete", { teamId }) as Promise<ActOnRequestBridgeResult>,
  presence: () => ipcRenderer.invoke("lol:presence") as Promise<PresenceBridgeResult>,
  readSummoner: () => ipcRenderer.invoke("lol:read-summoner") as Promise<ReadSummonerBridgeResult>,
  onLcuStatus: (callback) => {
    const listener = (_event: unknown, status: LcuStatusBridge): void => callback(status);
    ipcRenderer.on("lol:lcu-status", listener);
    return () => ipcRenderer.removeListener("lol:lcu-status", listener);
  },
  onAuthLost: (callback) => {
    const listener = (): void => callback();
    ipcRenderer.on("lol:auth-lost", listener);
    return () => ipcRenderer.removeListener("lol:auth-lost", listener);
  },
  onSessionRestored: (callback) => {
    const listener = (): void => callback();
    ipcRenderer.on("lol:session-restored", listener);
    return () => ipcRenderer.removeListener("lol:session-restored", listener);
  },
};

contextBridge.exposeInMainWorld("lol", bridge);
