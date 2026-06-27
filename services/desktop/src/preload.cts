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
}

/** LCU reachability badge state pushed from the main-process poller. */
export type LcuStatusBridge = "online" | "loading" | "offline";

/** Public API surface exposed on `window.lol`. */
export interface LolBridge {
  getSession(): Promise<SessionStateBridgeResult>;
  pair(code: string, deviceLabel?: string): Promise<PairBridgeResult>;
  refresh(): Promise<{ ok: boolean }>;
  verify(): Promise<VerifyBridgeResult>;
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
