/**
 * Electron preload — the ONLY bridge between the sandboxed renderer and the
 * privileged main process. The renderer runs with `contextIsolation:true` +
 * `nodeIntegration:false`, so it has no Node / network access; it asks the main
 * process to read the local LCU and POST to the backend via these IPC channels.
 *
 * Exposes `window.lol`:
 *   - `readSummoner()`  -> main reads the LCU current summoner (READ-ONLY).
 *   - `submitVerification({ code, region })` -> main reads the LCU then POSTs
 *     the verify-desktop request and returns the mapped result.
 *
 * Nothing privileged is leaked: only these two narrow, validated functions are
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

/** Result of `window.lol.submitVerification(...)`. */
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

/** Public API surface exposed on `window.lol`. */
export interface LolBridge {
  readSummoner(): Promise<ReadSummonerBridgeResult>;
  submitVerification(args: { code: string }): Promise<VerifyBridgeResult>;
}

const bridge: LolBridge = {
  readSummoner: () => ipcRenderer.invoke("lol:read-summoner") as Promise<ReadSummonerBridgeResult>,
  submitVerification: (args) =>
    ipcRenderer.invoke("lol:verify", args) as Promise<VerifyBridgeResult>,
};

contextBridge.exposeInMainWorld("lol", bridge);
