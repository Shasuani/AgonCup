/**
 * Desktop account-linking flow (main-process side) — step 2 of onboarding.
 *
 * Ties together the READ-ONLY LCU current-summoner read and the AUTHENTICATED
 * backend `POST /api/v1/accounts/verify-lcu`: the user is already signed in (a
 * pairing-code exchange minted the session — see auth/pairClient.ts), so this
 * call carries the access JWT as `Authorization: Bearer <token>` and NO code.
 * We read the local LCU Riot ID (gameName/tagLine) + region (proving the user is
 * signed into that account on this machine) and submit
 * `{ game_name, tag_line, region, lcu_puuid, summoner_id }`. The backend takes
 * the USER from the principal and resolves the CANONICAL 78-char PUUID server-
 * side via Account-V1 by-riot-id — that resolve (a 404 => Riot ID does not
 * exist) is the "double verification"; we never send the LCU PUUID as identity.
 *
 * SESSION BOUNDARY: a `401` means the access token expired — the caller should
 * silently refresh (see auth/pairClient.refreshSession) and retry once. The
 * status is surfaced verbatim so the main process can drive that retry.
 *
 * TRUST BOUNDARY: the LCU read is a POSSESSION signal (you must be signed into
 * that client); the advisory `lcu_puuid` is a secondary signal only. Resolving
 * the Riot ID proves it EXISTS, not that the submitter OWNS it — true ownership
 * is backstopped by cross-client result corroboration + admin override, never
 * this endpoint alone (PLAN.md §3.1 advisory-identity model).
 */

import { readCurrentSummoner, type ReadSummonerResult } from "./readSummoner.js";

/** Successful verification (account linked backend-side). */
export interface VerifyOk {
  readonly ok: true;
  readonly accountId: string;
  /** The CANONICAL 78-char Account-V1 PUUID the backend resolved. */
  readonly puuid: string;
  /** Solo/Duo rank tier (e.g. "GOLD"); undefined when unranked / fetch failed. */
  readonly tier?: string;
  /** Solo/Duo division (Riot "rank", e.g. "II"); undefined when unranked. */
  readonly division?: string;
  /** Solo/Duo league points; undefined when unranked / fetch failed. */
  readonly leaguePoints?: number;
  /** Display string e.g. "Gold II, 45 LP" or "Unranked". */
  readonly rankText?: string;
  /** Canonical Riot ID game name the backend resolved (for "linked X#Y" copy). */
  readonly gameName?: string;
  /** Canonical Riot ID tag line the backend resolved. */
  readonly tagLine?: string;
}

/** Failed verification, with an HTTP-ish status for the renderer to map. */
export interface VerifyErr {
  readonly ok: false;
  /** 0 => could not reach the LCU/backend; otherwise the backend HTTP status. */
  readonly status: number;
  readonly detail: string;
}

export type VerifyResult = VerifyOk | VerifyErr;

/** Inputs from the main process. The Riot ID + region are NOT supplied by the
 * user — they are read from the local League client (LCU). The access token
 * authenticates the call (the user is already paired); there is NO code. */
export interface VerifyArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
}

/** Injectable seams so the flow is unit-testable without Electron / network. */
export interface VerifyDeps {
  /** Read the local LCU current summoner (defaults to the real read). */
  readonly readSummoner?: () => Promise<ReadSummonerResult>;
  /** `fetch` implementation (defaults to global fetch). */
  readonly fetch?: typeof fetch;
  /** Backend base URL (defaults to BACKEND_URL env / localhost:8000). */
  readonly backendUrl?: string;
}

/** Resolve the backend base URL from explicit dep, env, or the dev default. */
function resolveBackendUrl(explicit?: string): string {
  if (explicit !== undefined && explicit !== "") {
    return explicit;
  }
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
    ?.env?.["BACKEND_URL"];
  return env !== undefined && env !== "" ? env : "http://localhost:8000";
}

/**
 * Read the LCU Riot ID + region then POST them (authenticated with the access
 * token) to the backend `verify-lcu` endpoint. The backend takes the user from
 * the JWT principal and resolves the canonical PUUID. Never throws — maps every
 * failure to a typed result. A `401` is surfaced so the caller can refresh +
 * retry.
 */
export async function verifyDesktop(
  args: VerifyArgs,
  deps: VerifyDeps = {},
): Promise<VerifyResult> {
  const read = deps.readSummoner ?? readCurrentSummoner;
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  const summoner = await read();
  if (!summoner.ok) {
    // Surface League-not-running distinctly via status 0 + reason in detail.
    return { ok: false, status: 0, detail: summoner.detail };
  }
  if (summoner.region === undefined || summoner.region === "") {
    return {
      ok: false,
      status: 0,
      detail: "Could not read your region from the League client. Make sure you're fully signed in.",
    };
  }
  if (summoner.gameName === "" || summoner.tagLine === "") {
    return {
      ok: false,
      status: 0,
      detail:
        "Could not read your Riot ID from the League client. Make sure you're fully signed in.",
    };
  }

  const body = JSON.stringify({
    game_name: summoner.gameName,
    tag_line: summoner.tagLine,
    region: summoner.region,
    lcu_puuid: summoner.puuid,
    summoner_id: summoner.summonerId !== "" ? summoner.summonerId : null,
  });

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/accounts/verify-lcu`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${args.accessToken}`,
      },
      body,
    });
  } catch (err) {
    return { ok: false, status: 0, detail: `Backend unreachable: ${String(err)}` };
  }

  let json: unknown;
  try {
    json = await res.json();
  } catch {
    json = undefined;
  }

  if (res.ok) {
    const rec = (json ?? {}) as {
      account_id?: unknown;
      puuid?: unknown;
      tier?: unknown;
      division?: unknown;
      league_points?: unknown;
      rank_text?: unknown;
      game_name?: unknown;
      tag_line?: unknown;
    };
    return {
      ok: true,
      accountId: typeof rec.account_id === "string" ? rec.account_id : "",
      puuid: typeof rec.puuid === "string" ? rec.puuid : summoner.puuid,
      ...(typeof rec.tier === "string" ? { tier: rec.tier } : {}),
      ...(typeof rec.division === "string" ? { division: rec.division } : {}),
      ...(typeof rec.league_points === "number" ? { leaguePoints: rec.league_points } : {}),
      ...(typeof rec.rank_text === "string" ? { rankText: rec.rank_text } : {}),
      ...(typeof rec.game_name === "string" ? { gameName: rec.game_name } : {}),
      ...(typeof rec.tag_line === "string" ? { tagLine: rec.tag_line } : {}),
    };
  }

  const detail =
    typeof (json as { detail?: unknown } | undefined)?.detail === "string"
      ? ((json as { detail: string }).detail)
      : `Verification failed (HTTP ${res.status}).`;
  return { ok: false, status: res.status, detail };
}
