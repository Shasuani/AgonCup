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
  /** ISO UTC scheduling times (null when unscheduled). */
  readonly registrationOpensAt: string | null;
  readonly startAt: string | null;
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
    registrationOpensAt:
      typeof rec["registration_opens_at"] === "string" ? rec["registration_opens_at"] : null,
    startAt: typeof rec["start_at"] === "string" ? rec["start_at"] : null,
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
  args: AuthedArgs & { code: string; teamId?: string },
  deps: HttpDeps = {},
): Promise<JoinTournamentResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  // team_id disambiguates WHICH captained team enters a 5v5 tournament; omitted
  // for solo modes or when the caller captains a single team (backend auto-resolves).
  const payload: { code: string; team_id?: string } = { code: args.code };
  if (args.teamId !== undefined && args.teamId !== "") {
    payload.team_id = args.teamId;
  }

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/tournaments/join-by-code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${args.accessToken}`,
      },
      body: JSON.stringify(payload),
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

// --- bracket (matches with team names) ------------------------------------- //

/** One match in a tournament bracket. */
export interface BracketMatch {
  readonly round: number;
  readonly slot: number;
  readonly teamAName: string | null;
  readonly teamBName: string | null;
  readonly winnerTeamId: string | null;
  readonly teamAId: string | null;
  readonly teamBId: string | null;
  readonly status: string;
}

export type BracketResult =
  | { readonly ok: true; readonly tournament: Tournament; readonly matches: BracketMatch[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asBracketMatch(raw: unknown): BracketMatch {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (k: string): string | null => (typeof r[k] === "string" ? (r[k] as string) : null);
  const n = (k: string): number => (typeof r[k] === "number" ? (r[k] as number) : 0);
  return {
    round: n("round"),
    slot: n("slot"),
    teamAName: s("team_a_name"),
    teamBName: s("team_b_name"),
    winnerTeamId: s("winner_team_id"),
    teamAId: s("team_a_id"),
    teamBId: s("team_b_id"),
    status: s("status") ?? "pending",
  };
}

/** The tournament's bracket (matches with team names). */
export async function getBracket(
  args: AuthedArgs & { tournamentId: string },
  deps: HttpDeps = {},
): Promise<BracketResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/bracket`,
      { method: "GET", headers: { Authorization: `Bearer ${args.accessToken}` } },
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
    const rec = (json ?? {}) as Record<string, unknown>;
    const list = Array.isArray(rec["matches"]) ? (rec["matches"] as unknown[]) : [];
    return { ok: true, tournament: asTournament(rec["tournament"]), matches: list.map(asBracketMatch) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the bracket (HTTP ${res.status}).`),
  };
}
