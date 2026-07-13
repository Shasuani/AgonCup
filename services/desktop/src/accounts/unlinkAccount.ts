/**
 * Unregister / unlink ONE of the signed-in user's Riot accounts
 * (`DELETE /api/v1/accounts/me/{account_id}`).
 *
 * Powers the Account screen's per-account "Unregister" affordance. Mirrors
 * {@link listMyAccounts}: injectable fetch/backendUrl, never throws, maps every
 * failure to a typed `{ ok:false, status, detail }` (status 0 = backend
 * unreachable), sends the access token as `Authorization: Bearer …`, and
 * camelises the snake_case body. A `401` is surfaced so the caller can refresh
 * the session and retry.
 */

import type { LinkedAccount } from "./listAccounts.js";

export type UnlinkAccountResult =
  | { readonly ok: true; readonly account: LinkedAccount }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface UnlinkAccountArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
  /** The `account_id` of the account to unlink. */
  readonly accountId: string;
}

export interface UnlinkAccountDeps {
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

function asLinkedAccount(raw: unknown): LinkedAccount {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    accountId: typeof rec["account_id"] === "string" ? rec["account_id"] : "",
    gameName: typeof rec["game_name"] === "string" ? rec["game_name"] : null,
    tagLine: typeof rec["tag_line"] === "string" ? rec["tag_line"] : null,
    region: typeof rec["region"] === "string" ? rec["region"] : null,
    verified: rec["verified"] === true,
    rankText: typeof rec["rank_text"] === "string" ? rec["rank_text"] : "Unranked",
  };
}

export async function unlinkAccount(
  args: UnlinkAccountArgs,
  deps: UnlinkAccountDeps = {},
): Promise<UnlinkAccountResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/accounts/me/${encodeURIComponent(args.accountId)}`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${args.accessToken}` },
      },
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
      : `Could not unregister the account (HTTP ${res.status}).`;
  return { ok: false, status: res.status, detail };
}
