/**
 * Tournament reads/writes for the signed-in user (`/api/v1/tournaments/*`).
 *
 * `listMyTournaments` powers the "My tournaments" list; `joinTournamentByCode`
 * consumes a join code. Both mirror {@link listMyAccounts}: injectable
 * fetch/backendUrl, never throw, map every failure to a typed
 * `{ ok:false, status, detail }` (status 0 = backend unreachable), send the
 * access token as `Authorization: Bearer …`, and camelise the snake_case body.
 * A `401` is surfaced so the caller can refresh the session and retry.
 */

/** One tournament as `/tournaments/mine` + `/tournaments/join-by-code` return it. */
export interface Tournament {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly format: string;
  readonly gameMode: string;
  readonly bestOf: number;
  readonly maxTeams: number;
  readonly isSharedToCentral: boolean;
}

export type ListTournamentsResult =
  | { readonly ok: true; readonly tournaments: Tournament[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type JoinTournamentResult =
  | { readonly ok: true; readonly tournament: Tournament }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface AuthedArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
}

export interface HttpDeps {
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

function detailOf(json: unknown, fallback: string): string {
  const d = (json as { detail?: unknown } | undefined)?.detail;
  return typeof d === "string" && d !== "" ? d : fallback;
}

function asTournament(raw: unknown): Tournament {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    id: typeof rec["id"] === "string" ? rec["id"] : "",
    name: typeof rec["name"] === "string" ? rec["name"] : "",
    status: typeof rec["status"] === "string" ? rec["status"] : "",
    format: typeof rec["format"] === "string" ? rec["format"] : "",
    gameMode: typeof rec["game_mode"] === "string" ? rec["game_mode"] : "",
    bestOf: typeof rec["best_of"] === "number" ? rec["best_of"] : 1,
    maxTeams: typeof rec["max_teams"] === "number" ? rec["max_teams"] : 0,
    isSharedToCentral: rec["is_shared_to_central"] === true,
  };
}

export async function listMyTournaments(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<ListTournamentsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/tournaments/mine`, {
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
    return { ok: true, tournaments: list.map(asTournament) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load your tournaments (HTTP ${res.status}).`),
  };
}

export async function joinTournamentByCode(
  args: AuthedArgs & { code: string },
  deps: HttpDeps = {},
): Promise<JoinTournamentResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/tournaments/join-by-code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${args.accessToken}`,
      },
      body: JSON.stringify({ code: args.code }),
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
    return { ok: true, tournament: asTournament(json) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not join the tournament (HTTP ${res.status}).`),
  };
}
