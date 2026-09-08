/**
 * Refresh one linked account's Solo/Duo rank from Riot
 * (`POST /api/v1/accounts/me/{accountId}/rank`).
 *
 * The renderer calls this the moment it sees which account is signed in to the
 * League client (and periodically after), so the rank it shows is LIVE rather
 * than the snapshot taken at verify time. The backend re-reads League-V4, stores
 * the result and answers with the account row; it throttles and never fails on
 * a Riot error (a stale rank comes back instead). Never throws; maps every
 * failure to a typed result. A `401` is surfaced so the caller can refresh the
 * session and retry (mirrors `listMyAccounts`).
 */

import { asLinkedAccount, type LinkedAccount } from "./listAccounts.js";

export type RefreshRankResult =
  | { readonly ok: true; readonly account: LinkedAccount }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface RefreshRankArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
  /** The linked account's id, as `GET /accounts/me` returned it. */
  readonly accountId: string;
}

export interface RefreshRankDeps {
  readonly fetch?: typeof fetch;
  readonly backendUrl?: string;
}

function resolveBackendUrl(explicit?: string): string {
  if (explicit !== undefined && explicit !== "") {
    return explicit;
  }
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
    ?.env?.["BACKEND_URL"];
  return env !== undefined && env !== "" ? env : "http://localhost:8000";
}

export async function refreshAccountRank(
  args: RefreshRankArgs,
  deps: RefreshRankDeps = {},
): Promise<RefreshRankResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);
  if (args.accountId === "") {
    return { ok: false, status: 0, detail: "No account to refresh." };
  }

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/accounts/me/${encodeURIComponent(args.accountId)}/rank`,
      { method: "POST", headers: { Authorization: `Bearer ${args.accessToken}` } },
    );
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
    return { ok: true, account: asLinkedAccount(json) };
  }

  const detail =
    typeof (json as { detail?: unknown } | undefined)?.detail === "string"
      ? (json as { detail: string }).detail
      : `Could not refresh the rank (HTTP ${res.status}).`;
  return { ok: false, status: res.status, detail };
}
