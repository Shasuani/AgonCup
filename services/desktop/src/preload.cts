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
}

export type ListTournamentsBridgeResult =
  | { ok: true; tournaments: TournamentBridge[] }
  | { ok: false; status: number; detail: string };

export type JoinTournamentBridgeResult =
  | { ok: true; tournament: TournamentBridge }
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
  /** The signed-in user's tournaments. */
  listMyTournaments(): Promise<ListTournamentsBridgeResult>;
  /** Join a tournament by its code. */
  joinTournament(code: string): Promise<JoinTournamentBridgeResult>;
  /** Create a team (acting user becomes captain; response carries the join code). */
  createTeam(name: string): Promise<CreateTeamBridgeResult>;
  /** Join a team by its code. */
  joinTeam(code: string): Promise<JoinTeamBridgeResult>;
  /** A team's roster. */
  teamMembers(teamId: string): Promise<TeamMembersBridgeResult>;
  /** A team's join code (captain-only). */
  teamJoinCode(teamId: string): Promise<TeamJoinCodeBridgeResult>;
  /** Global live presence (who is in a game + which server). */
  presence(): Promise<PresenceBridgeResult>;
  readSummoner(): Promise<ReadSummonerBridgeResult>;
  /** Subscribe to LCU badge updates. Returns an unsubscribe function. */
  onLcuStatus(callback: (status: LcuStatusBridge) => void): () => void;
  /** Subscribe to "session expired" events. Returns an unsubscribe function. */
  onAuthLost(callback: () => void): () => void;
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
  listMyTournaments: () =>
    ipcRenderer.invoke("lol:tournaments-mine") as Promise<ListTournamentsBridgeResult>,
  joinTournament: (code) =>
    ipcRenderer.invoke("lol:tournament-join", { code }) as Promise<JoinTournamentBridgeResult>,
  createTeam: (name) =>
    ipcRenderer.invoke("lol:team-create", { name }) as Promise<CreateTeamBridgeResult>,
  joinTeam: (code) =>
    ipcRenderer.invoke("lol:team-join", { code }) as Promise<JoinTeamBridgeResult>,
  teamMembers: (teamId) =>
    ipcRenderer.invoke("lol:team-members", { teamId }) as Promise<TeamMembersBridgeResult>,
  teamJoinCode: (teamId) =>
    ipcRenderer.invoke("lol:team-code", { teamId }) as Promise<TeamJoinCodeBridgeResult>,
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
};

contextBridge.exposeInMainWorld("lol", bridge);
