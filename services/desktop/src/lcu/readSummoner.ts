/**
 * READ-ONLY current-summoner read for the desktop verify flow.
 *
 * Builds an {@link LcuAdapterImpl} over the real {@link LcuRuntime} and calls
 * `getCurrentSummoner()` to obtain the locally signed-in account's Riot ID
 * (gameName/tagLine) + PUUID + summonerId from
 * `GET /lol-summoner/v1/current-summoner`, plus the platform route from
 * `/riotclient/region-locale`.
 *
 * SAFETY: this path is READ-ONLY. The adapter is constructed with
 * `writeAutomationOptIn:false` and the kill-switch engaged; reads do not pass
 * through the write gate, and no write method (createCustomLobby / invite /
 * autoAccept / start) is ever called here. The Riot ID this returns is fed to
 * the backend verify-desktop endpoint, which resolves the canonical PUUID via
 * Account-V1 — see the trust-boundary note in the desktop verify handler / docs.
 *
 * Failures (League not running, lockfile missing, LCU drift/error) are mapped to
 * a typed `{ ok:false, reason }` result so the renderer can show a friendly
 * message rather than a stack trace.
 *
 * The Riot ID this returns feeds the authenticated `verify-lcu` endpoint (see
 * lcu/verify.ts), which resolves the canonical PUUID via Account-V1.
 */

import {
  LcuAdapterImpl,
  LcuResponseError,
  type LcuAdapterConfig,
  type LcuRuntime,
  type LcuSchemaVersion,
} from "@lol/lcu";

import { createRealLcuRuntime } from "./realRuntime.js";

/** Successful read of the local LCU current summoner. */
export interface CurrentSummonerOk {
  readonly ok: true;
  readonly puuid: string;
  readonly summonerId: string;
  /** EMPTY on modern clients — kept for back-compat, never the identity. */
  readonly displayName: string;
  /** Riot ID game name (reliable; the canonical identity the backend resolves). */
  readonly gameName: string;
  /** Riot ID tag line (reliable; pairs with {@link gameName}). */
  readonly tagLine: string;
  /** Platform route read from the LCU (e.g. "euw1"); undefined if unreadable. */
  readonly region: string | undefined;
}

/** Riot region label (from /riotclient/region-locale `region`) -> platform route. */
const _REGION_LABEL_TO_PLATFORM: Readonly<Record<string, string>> = {
  NA: "na1",
  EUW: "euw1",
  EUNE: "eun1",
  BR: "br1",
  LAN: "la1",
  LAS: "la2",
  OCE: "oc1",
  OC: "oc1",
  TR: "tr1",
  RU: "ru",
  JP: "jp1",
};

/** Riot web-region (from /riotclient/region-locale `webRegion`) -> platform route. */
const _WEB_REGION_TO_PLATFORM: Readonly<Record<string, string>> = {
  na: "na1",
  euw: "euw1",
  eune: "eun1",
  br: "br1",
  lan: "la1",
  las: "la2",
  oce: "oc1",
  oc: "oc1",
  tr: "tr1",
  ru: "ru",
  jp: "jp1",
};

/** Why the current-summoner read failed (renderer maps to a friendly message). */
export type ReadSummonerFailureReason = "league_not_running" | "lcu_error";

/** Failed read of the local LCU current summoner. */
export interface CurrentSummonerErr {
  readonly ok: false;
  readonly reason: ReadSummonerFailureReason;
  readonly detail: string;
}

export type ReadSummonerResult = CurrentSummonerOk | CurrentSummonerErr;

/**
 * Read-only adapter config: writes are OFF and the kill-switch is engaged so
 * the adapter physically cannot perform a write even if asked. Pacing values
 * are inert (never exercised on the read path). The schema version is a
 * placeholder — it only stamps emitted reports, which this read path never
 * produces.
 */
const READ_ONLY_CONFIG: LcuAdapterConfig = {
  writeAutomationOptIn: false,
  killSwitchEngaged: true,
  requirePresenceForWrites: true,
  pacing: {
    minWriteDelayMs: 0,
    writeJitterMs: 0,
    maxWritesPerMinute: 0,
    maxBackoffMs: 0,
    maxRetryAttempts: 0,
  },
  schemaVersion: "0.0.0-phase0" as LcuSchemaVersion,
};

/** Node `ENOENT`-style error shape for lockfile-missing detection. */
function isFileNotFound(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { code?: unknown }).code === "ENOENT"
  );
}

/** Connection-refused / network errors mean the LCU process isn't listening. */
function isConnectionError(err: unknown): boolean {
  if (typeof err !== "object" || err === null) {
    return false;
  }
  const code = (err as { code?: unknown }).code;
  return (
    code === "ECONNREFUSED" ||
    code === "ECONNRESET" ||
    code === "EHOSTUNREACH" ||
    code === "ENOTFOUND"
  );
}

/**
 * Read the local LCU current summoner, READ-ONLY. Returns a typed result; never
 * throws. `runtime` is injectable for tests (defaults to the real Node runtime).
 */
export async function readCurrentSummoner(
  runtime: LcuRuntime = createRealLcuRuntime(),
): Promise<ReadSummonerResult> {
  const adapter = new LcuAdapterImpl({ config: READ_ONLY_CONFIG, runtime });
  try {
    const summoner = await adapter.getCurrentSummoner();
    // Read the platform/region from the LCU too (best-effort; the user never
    // picks it). Non-fatal: a missing region surfaces later as a clear message.
    const region = await readRegion(adapter, runtime);
    return {
      ok: true,
      puuid: summoner.puuid,
      summonerId: summoner.summonerId,
      displayName: summoner.displayName,
      gameName: summoner.gameName,
      tagLine: summoner.tagLine,
      region,
    };
  } catch (err) {
    // Lockfile missing or connection refused => League client isn't running.
    if (isFileNotFound(err) || isConnectionError(err)) {
      return {
        ok: false,
        reason: "league_not_running",
        detail: "League client is not running or not signed in.",
      };
    }
    // An LCU HTTP error (e.g. not signed in yet) — treat 404 as not-running.
    if (err instanceof LcuResponseError) {
      if (err.status === 404) {
        return {
          ok: false,
          reason: "league_not_running",
          detail: "No summoner is signed in yet.",
        };
      }
      return { ok: false, reason: "lcu_error", detail: err.message };
    }
    return { ok: false, reason: "lcu_error", detail: String(err) };
  }
}

/**
 * Read the local account's platform route from the LCU (READ-ONLY), so the user
 * never has to pick a region. The LIVE LCU finding is that
 * `/lol-login/v1/session` `platformId` is NULL on modern clients, so
 * `/riotclient/region-locale` is now the PRIMARY source: prefer its `webRegion`
 * (e.g. "eune" -> "eun1"), then its `region` label (e.g. "EUNE" -> "eun1"), and
 * only then fall back to the (often-null) session `platformId` (e.g. "EUW1" ->
 * "euw1"). Returns undefined if none is readable (caller treats that as a
 * friendly "sign in fully" error). Never throws.
 */
async function readRegion(
  adapter: LcuAdapterImpl,
  runtime: LcuRuntime,
): Promise<string | undefined> {
  let auth: { port: number; password: string };
  try {
    auth = await adapter.readLockfileAuth();
  } catch {
    return undefined;
  }
  const base = `https://127.0.0.1:${auth.port}`;
  const header = `Basic ${Buffer.from(`riot:${auth.password}`).toString("base64")}`;
  const get = async (path: string): Promise<unknown> => {
    try {
      const res = await runtime.http.request({
        method: "GET",
        url: `${base}${path}`,
        headers: { Authorization: header, Accept: "application/json" },
      });
      return res.status === 200 ? JSON.parse(res.body) : undefined;
    } catch {
      return undefined;
    }
  };

  // PRIMARY: /riotclient/region-locale (webRegion preferred, then region label).
  const locale = (await get("/riotclient/region-locale")) as
    | { webRegion?: unknown; region?: unknown }
    | undefined;
  if (typeof locale?.webRegion === "string" && locale.webRegion.length > 0) {
    const platform = _WEB_REGION_TO_PLATFORM[locale.webRegion.toLowerCase()];
    if (platform !== undefined) {
      return platform;
    }
  }
  if (typeof locale?.region === "string" && locale.region.length > 0) {
    const platform = _REGION_LABEL_TO_PLATFORM[locale.region.toUpperCase()];
    if (platform !== undefined) {
      return platform;
    }
  }
  // FALLBACK: /lol-login/v1/session platformId (NULL on modern clients).
  const session = (await get("/lol-login/v1/session")) as { platformId?: unknown } | undefined;
  if (typeof session?.platformId === "string" && session.platformId.length > 0) {
    return session.platformId.toLowerCase();
  }
  return undefined;
}
