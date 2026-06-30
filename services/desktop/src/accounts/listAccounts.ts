/**
 * List the signed-in user's OWN linked Riot accounts (`GET /api/v1/accounts/me`).
 *
 * The authenticated read the renderer uses to show "your linked accounts" and the
 * persistent "Link another account" affordance — so on a cold reopen (already
 * signed in) the client can populate the list without re-verifying. Never throws;
 * maps every failure to a typed result. A `401` is surfaced so the caller can
 * refresh the session and retry (mirrors `verifyDesktop`).
 */

/** One linked account as the backend's `/accounts/me` returns it (camelised). */
export interface LinkedAccount {
  readonly accountId: string;
  readonly gameName: string | null;
  readonly tagLine: string | null;
  readonly region: string | null;
  readonly verified: boolean;
  readonly rankText: string;
}

export type ListAccountsResult =
  | { readonly ok: true; readonly accounts: LinkedAccount[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface ListAccountsArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
}

export interface ListAccountsDeps {
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

export async function listMyAccounts(
  args: ListAccountsArgs,
  deps: ListAccountsDeps = {},
): Promise<ListAccountsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/accounts/me`, {
      method: "GET",
      headers: { Authorization: `Bearer ${args.accessToken}` },
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
    const list = Array.isArray(json) ? json : [];
    return { ok: true, accounts: list.map(asLinkedAccount) };
  }

  const detail =
    typeof (json as { detail?: unknown } | undefined)?.detail === "string"
      ? (json as { detail: string }).detail
      : `Could not load your accounts (HTTP ${res.status}).`;
  return { ok: false, status: res.status, detail };
}
