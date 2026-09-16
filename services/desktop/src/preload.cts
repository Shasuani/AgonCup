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
  /** Summoner icon id + account level (display only). */
  profileIconId?: number;
  summonerLevel?: number;
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

/** Result of `window.lol.refreshRank(id)`: the account with its LIVE Solo/Duo rank. */
export type RefreshRankBridgeResult =
  | { ok: true; account: LinkedAccountBridge }
  | { ok: false; status: number; detail: string };

/** One tournament in the signed-in user's list / a join result. */
export interface TournamentBridge {
  id: string;
  /** The tournament's server; sent back as `X-Guild-Id` on every per-tournament call. */
  guildId: string;
  name: string;
  status: string;
  format: string;
  gameMode: string;
  bestOf: number;
  maxTeams: number;
  isSharedToCentral: boolean;
  /** Server the tournament is played on (null = not set). */
  region: string | null;
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
  guildId: string;
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
  /** The team's server; sent back as `X-Guild-Id` on every per-team call. */
  guildId: string;
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

/** One row of a live scoreboard (display name, never a Riot ID). */
export interface LiveBoardPlayerBridge {
  name: string;
  championKey: string;
  championName: string;
  team: string;
  level: number;
  isDead: boolean;
  respawnTimer: number;
  items: number[];
  kills: number;
  deaths: number;
  assists: number;
  creepScore: number;
  wardScore: number;
  spell1Key: string | null;
  spell2Key: string | null;
}

export type LiveBoardBridgeResult =
  | {
      ok: true;
      board: { matchId: string; gameTime: number; gameMode: string; updatedAt: string; players: LiveBoardPlayerBridge[] };
    }
  | { ok: false; status: number; detail: string };

export type PresenceBridgeResult =
  | { ok: true; playersInGame: number; entries: PresenceEntryBridge[] }
  | { ok: false; status: number; detail: string };

/** One scoreboard line of a played game (display-only, from the eog reports). */
export interface HistoryPlayerBridge {
  puuid: string;
  displayName: string | null;
  riotId: string | null;
  championId: number;
  kills: number;
  deaths: number;
  assists: number;
  championName: string | null;
  level: number | null;
  items: number[];
  spell1Id: number | null;
  spell2Id: number | null;
  goldEarned: number | null;
  minionsKilled: number | null;
  damageToChampions: number | null;
  visionScore: number | null;
  profileIconId: number | null;
}

/** The last summoner this device saw signed in to League (Riot ID, region,
 * icon/level) — display only; what the UI shows while League is closed. */
export type ProfileBridgeResult = {
  ok: true;
  gameName?: string;
  tagLine?: string;
  region?: string;
  profileIconId?: number;
  summonerLevel?: number;
};

export interface HistoryTeamBridge {
  isWinner: boolean;
  players: HistoryPlayerBridge[];
}

export interface HistoryGameBridge {
  matchId: string;
  tournamentId: string;
  tournamentName: string | null;
  round: number | null;
  lcuGameId: string;
  playedAt: string;
  teams: HistoryTeamBridge[];
}

export type HistoryBridgeResult =
  | { ok: true; games: HistoryGameBridge[] }
  | { ok: false; status: number; detail: string };

/** LCU reachability badge state pushed from the main-process poller. */
export type LcuStatusBridge = "online" | "loading" | "offline";

/** One champ-select seat as shown in the renderer (display labels only). */
export interface ChampSelectMemberBridge {
  cellId: number;
  side: "my" | "their";
  gameName?: string;
  championId: number;
  championName?: string;
  locked: boolean;
}

/** The live champ-select board (`null` = the client is not in champ select). */
export interface ChampSelectBridge {
  timerPhase?: string;
  members: ChampSelectMemberBridge[];
}

/** First blood on the live-game banner (decides a 1v1 ARAM duel). */
export interface LiveGameFirstBloodBridge {
  scorer: string;
  /** Whether the scorer is THIS player; null when the local name is unknown. */
  isYou: boolean | null;
  atSeconds: number;
}

/** The running (or just-finished) game (`null` = nothing to show). */
export interface LiveGameBridge {
  status: "in_game" | "ended";
  firstBlood: LiveGameFirstBloodBridge | null;
  /** The LOCAL player's outcome from the GameEnd event; null until known. */
  result: "win" | "loss" | null;
}

/** Whether this session may drive the League client (per-session write opt-in). */
export type AutomationBridgeResult = { ok: true; enabled: boolean };

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
  /** Re-read one linked account's Solo/Duo rank from Riot (live, throttled server-side). */
  refreshRank(accountId: string): Promise<RefreshRankBridgeResult>;
  /** Clear the local session (sign out) → next boot / view shows pairing (step 1). */
  signOut(): Promise<{ ok: boolean }>;
  /** The signed-in user's tournaments. */
  listMyTournaments(): Promise<ListTournamentsBridgeResult>;
  /** Live matches + lobby state; shape mirrors the renderer bridge's MyMatch. */
  listMyMatches(): Promise<unknown>;
  /** Join a tournament by its code. `teamId` picks the captained team for 5v5. */
  joinTournament(code: string, teamId?: string): Promise<JoinTournamentBridgeResult>;
  /** Clear a FINISHED tournament off this user's own list (per-user, not a delete).
   *  `guildId` (the row's own server) rides along on this and every per-object
   *  call below: the lists span every server the user belongs to. */
  hideTournament(tournamentId: string, guildId?: string): Promise<HideTournamentBridgeResult>;
  /** The tournament's bracket (matches with team names). */
  tournamentBracket(tournamentId: string, guildId?: string): Promise<BracketBridgeResult>;
  /** Look up a join code without enrolling (to learn the game mode first). */
  lookupTournament(code: string): Promise<JoinTournamentBridgeResult>;
  /** Who has entered a tournament, and who has locked in. */
  tournamentEntrants(tournamentId: string, guildId?: string): Promise<EntrantsBridgeResult>;
  /** Lock in for a tournament. `teamId` only when captaining several teams. */
  tournamentLockIn(tournamentId: string, teamId?: string, guildId?: string): Promise<LockInBridgeResult>;
  /** Mark the caller's team ready for a match (team captain). */
  matchReady(matchId: string, guildId?: string): Promise<ReadyBridgeResult>;
  /** Create a team (acting user becomes captain; response carries the join code). */
  createTeam(name: string): Promise<CreateTeamBridgeResult>;
  /** Join a team by its code. */
  joinTeam(code: string): Promise<JoinTeamBridgeResult>;
  /** A team's roster. */
  teamMembers(teamId: string, guildId?: string): Promise<TeamMembersBridgeResult>;
  /** A team's join code (captain-only). */
  teamJoinCode(teamId: string, guildId?: string): Promise<TeamJoinCodeBridgeResult>;
  /** Pending join requests across every team the signed-in user captains. */
  teamJoinRequests(): Promise<TeamJoinRequestsBridgeResult>;
  /** Approve a pending join request (captain of that team). */
  approveJoinRequest(teamId: string, userId: string, guildId?: string): Promise<ActOnRequestBridgeResult>;
  /** Reject a pending join request (captain of that team). */
  rejectJoinRequest(teamId: string, userId: string, guildId?: string): Promise<ActOnRequestBridgeResult>;
  /** Remove a member from a team (captain of that team). */
  removeMember(teamId: string, userId: string, guildId?: string): Promise<ActOnRequestBridgeResult>;
  /** Teams the signed-in user belongs to (with their role + status). */
  myTeams(): Promise<MyTeamsBridgeResult>;
  /** Leave a team (self-service; captains must transfer/disband first). */
  leaveTeam(teamId: string, guildId?: string): Promise<ActOnRequestBridgeResult>;
  /** Disband a team you captain (deletes the team + its roster). */
  deleteTeam(teamId: string, guildId?: string): Promise<ActOnRequestBridgeResult>;
  /** Global live presence (who is in a game + which server). */
  presence(): Promise<PresenceBridgeResult>;
  /** The live scoreboard of a game in progress (404 until a client sent one). */
  liveBoard(matchId: string): Promise<LiveBoardBridgeResult>;
  /** The signed-in player's recent games (per-game scoreboards). */
  myHistory(): Promise<HistoryBridgeResult>;
  /** The last summoner icon/level this device saw (works with League closed). */
  getProfile(): Promise<ProfileBridgeResult>;
  /** The self-update state (current version, latest, phase, progress). */
  getUpdateState(): Promise<UpdateStateBridge>;
  /** Ask the backend now ("Check for updates"); resolves with the new state. */
  checkForUpdates(): Promise<UpdateStateBridge>;
  /** Download (if needed) and install the update; the app restarts itself. */
  installUpdate(): Promise<UpdateStateBridge>;
  /** Subscribe to update-state changes. Returns an unsubscribe function. */
  onUpdate(callback: (state: UpdateStateBridge) => void): () => void;
  /** Open an http(s) link in the system browser (the manual installer). */
  openExternal(url: string): Promise<{ ok: true } | { ok: false; detail: string }>;
  /** Is this session allowed to drive the League client (lobby writes)? */
  getAutomation(): Promise<AutomationBridgeResult>;
  /** Grant/revoke that permission for this session only (never persisted). */
  setAutomation(enabled: boolean): Promise<AutomationBridgeResult>;
  readSummoner(): Promise<ReadSummonerBridgeResult>;
  /** Subscribe to LCU badge updates. Returns an unsubscribe function. */
  onLcuStatus(callback: (status: LcuStatusBridge) => void): () => void;
  /** The current badge state (`null` before the first probe), for a screen that mounts after the last push. */
  getLcuStatus(): Promise<LcuStatusBridge | null>;
  /** Subscribe to live champ-select board updates (`null` = not in select). */
  onChampSelect(callback: (board: ChampSelectBridge | null) => void): () => void;
  /** Subscribe to live-game winner-banner updates (`null` = nothing to show). */
  onLiveGame(callback: (view: LiveGameBridge | null) => void): () => void;
  /** The current live-game view, for a screen that mounts after the last push. */
  getLiveGame(): Promise<LiveGameBridge | null>;
  /** Subscribe to "session expired" events. Returns an unsubscribe function. */
  onAuthLost(callback: () => void): () => void;
  /** Subscribe to "a background retry got the session back" events, so a screen
   * showing "reconnecting…" can leave it. Returns an unsubscribe function. */
  onSessionRestored(callback: () => void): () => void;
}

/** Mirror of `UpdateState` in src/update/coordinator.ts (keep in sync). */
export interface UpdateStateBridge {
  current: string;
  platform: string;
  supported: boolean;
  phase:
    | "idle"
    | "checking"
    | "up_to_date"
    | "unknown"
    | "available"
    | "downloading"
    | "ready"
    | "installing"
    | "failed";
  latest: string | null;
  required: boolean;
  downloadUrl: string | null;
  progress: number | null;
  error: string | null;
  lastCheckedAt: string | null;
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
  refreshRank: (accountId) =>
    ipcRenderer.invoke("lol:account-rank-refresh", { accountId }) as Promise<RefreshRankBridgeResult>,
  signOut: () => ipcRenderer.invoke("lol:sign-out") as Promise<{ ok: boolean }>,
  listMyTournaments: () =>
    ipcRenderer.invoke("lol:tournaments-mine") as Promise<ListTournamentsBridgeResult>,
  listMyMatches: () => ipcRenderer.invoke("lol:matches-mine") as Promise<unknown>,
  joinTournament: (code, teamId) =>
    ipcRenderer.invoke("lol:tournament-join", { code, teamId }) as Promise<JoinTournamentBridgeResult>,
  hideTournament: (tournamentId, guildId) =>
    ipcRenderer.invoke("lol:tournament-hide", { tournamentId, guildId }) as Promise<HideTournamentBridgeResult>,
  tournamentBracket: (tournamentId, guildId) =>
    ipcRenderer.invoke("lol:tournament-bracket", { tournamentId, guildId }) as Promise<BracketBridgeResult>,
  lookupTournament: (code) =>
    ipcRenderer.invoke("lol:tournament-lookup", { code }) as Promise<JoinTournamentBridgeResult>,
  tournamentEntrants: (tournamentId, guildId) =>
    ipcRenderer.invoke("lol:tournament-entrants", { tournamentId, guildId }) as Promise<EntrantsBridgeResult>,
  tournamentLockIn: (tournamentId, teamId, guildId) =>
    ipcRenderer.invoke("lol:tournament-lock-in", {
      tournamentId,
      teamId,
      guildId,
    }) as Promise<LockInBridgeResult>,
  matchReady: (matchId, guildId) =>
    ipcRenderer.invoke("lol:match-ready", { matchId, guildId }) as Promise<ReadyBridgeResult>,
  createTeam: (name) =>
    ipcRenderer.invoke("lol:team-create", { name }) as Promise<CreateTeamBridgeResult>,
  joinTeam: (code) =>
    ipcRenderer.invoke("lol:team-join", { code }) as Promise<JoinTeamBridgeResult>,
  teamMembers: (teamId, guildId) =>
    ipcRenderer.invoke("lol:team-members", { teamId, guildId }) as Promise<TeamMembersBridgeResult>,
  teamJoinCode: (teamId, guildId) =>
    ipcRenderer.invoke("lol:team-code", { teamId, guildId }) as Promise<TeamJoinCodeBridgeResult>,
  teamJoinRequests: () =>
    ipcRenderer.invoke("lol:team-join-requests") as Promise<TeamJoinRequestsBridgeResult>,
  approveJoinRequest: (teamId, userId, guildId) =>
    ipcRenderer.invoke("lol:team-approve", { teamId, userId, guildId }) as Promise<ActOnRequestBridgeResult>,
  rejectJoinRequest: (teamId, userId, guildId) =>
    ipcRenderer.invoke("lol:team-reject", { teamId, userId, guildId }) as Promise<ActOnRequestBridgeResult>,
  removeMember: (teamId, userId, guildId) =>
    ipcRenderer.invoke("lol:team-remove-member", { teamId, userId, guildId }) as Promise<ActOnRequestBridgeResult>,
  myTeams: () => ipcRenderer.invoke("lol:teams-mine") as Promise<MyTeamsBridgeResult>,
  leaveTeam: (teamId, guildId) =>
    ipcRenderer.invoke("lol:team-leave", { teamId, guildId }) as Promise<ActOnRequestBridgeResult>,
  deleteTeam: (teamId, guildId) =>
    ipcRenderer.invoke("lol:team-delete", { teamId, guildId }) as Promise<ActOnRequestBridgeResult>,
  presence: () => ipcRenderer.invoke("lol:presence") as Promise<PresenceBridgeResult>,
  liveBoard: (matchId) => ipcRenderer.invoke("lol:live-board", { matchId }) as Promise<LiveBoardBridgeResult>,
  myHistory: () => ipcRenderer.invoke("lol:history-mine") as Promise<HistoryBridgeResult>,
  getProfile: () => ipcRenderer.invoke("lol:profile-get") as Promise<ProfileBridgeResult>,
  getUpdateState: () => ipcRenderer.invoke("lol:update-state") as Promise<UpdateStateBridge>,
  checkForUpdates: () => ipcRenderer.invoke("lol:update-check") as Promise<UpdateStateBridge>,
  installUpdate: () => ipcRenderer.invoke("lol:update-install") as Promise<UpdateStateBridge>,
  onUpdate: (callback) => {
    const listener = (_event: unknown, state: UpdateStateBridge): void => callback(state);
    ipcRenderer.on("lol:update", listener);
    return () => ipcRenderer.removeListener("lol:update", listener);
  },
  openExternal: (url) =>
    ipcRenderer.invoke("lol:open-external", { url }) as Promise<{ ok: true } | { ok: false; detail: string }>,
  getAutomation: () => ipcRenderer.invoke("lol:automation-get") as Promise<AutomationBridgeResult>,
  setAutomation: (enabled) =>
    ipcRenderer.invoke("lol:automation-set", { enabled }) as Promise<AutomationBridgeResult>,
  readSummoner: () => ipcRenderer.invoke("lol:read-summoner") as Promise<ReadSummonerBridgeResult>,
  onLcuStatus: (callback) => {
    const listener = (_event: unknown, status: LcuStatusBridge): void => callback(status);
    ipcRenderer.on("lol:lcu-status", listener);
    return () => ipcRenderer.removeListener("lol:lcu-status", listener);
  },
  getLcuStatus: () => ipcRenderer.invoke("lol:lcu-status-get") as Promise<LcuStatusBridge | null>,
  onChampSelect: (callback) => {
    const listener = (_event: unknown, board: ChampSelectBridge | null): void => callback(board);
    ipcRenderer.on("lol:champ-select", listener);
    return () => ipcRenderer.removeListener("lol:champ-select", listener);
  },
  onLiveGame: (callback) => {
    const listener = (_event: unknown, view: LiveGameBridge | null): void => callback(view);
    ipcRenderer.on("lol:live-game", listener);
    return () => ipcRenderer.removeListener("lol:live-game", listener);
  },
  getLiveGame: () => ipcRenderer.invoke("lol:live-game-get") as Promise<LiveGameBridge | null>,
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
