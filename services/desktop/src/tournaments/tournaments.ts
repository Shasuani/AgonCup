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

/** Look up what a join code points at, without enrolling (GET /by-code/{code}).
 *
 * The client must know the game mode first: a 5v5 code has to ask a captain of
 * several teams which one is entering; a 1v1 code must never ask.
 */
export async function lookupTournamentByCode(
  args: AuthedArgs & { code: string },
  deps: HttpDeps = {},
): Promise<JoinTournamentResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/by-code/${encodeURIComponent(args.code)}`,
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
    return { ok: true, tournament: asTournament(json) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not find that join code (HTTP ${res.status}).`),
  };
}

// --- entrants + lock-in ---------------------------------------------------- //

/** One entry in a tournament: a team in 5v5, a player (their solo team) in 1v1. */
export interface Entrant {
  readonly teamId: string;
  readonly name: string;
  readonly lockedIn: boolean;
  readonly isSolo: boolean;
  /** True for the caller's own entry (their solo team in 1v1, their team in 5v5). */
  readonly isYou: boolean;
  /** True when the caller may lock this entry in (its captain / the solo player). */
  readonly canLockIn: boolean;
}

export type EntrantsResult =
  | { readonly ok: true; readonly entrants: Entrant[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type LockInResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asEntrant(raw: unknown): Entrant {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    teamId: typeof r["team_id"] === "string" ? (r["team_id"] as string) : "",
    name: typeof r["name"] === "string" ? (r["name"] as string) : "",
    lockedIn: r["locked_in"] === true,
    isSolo: r["is_solo"] === true,
    isYou: r["is_you"] === true,
    canLockIn: r["can_lock_in"] === true,
  };
}

/** Who has entered a tournament, and who has locked in (GET /{id}/entrants). */
export async function listEntrants(
  args: AuthedArgs & { tournamentId: string },
  deps: HttpDeps = {},
): Promise<EntrantsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/entrants`,
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
    const list = Array.isArray(json) ? json : [];
    return { ok: true, entrants: list.map(asEntrant) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the entrants (HTTP ${res.status}).`),
  };
}

/** Lock in for a tournament (POST /{id}/check-in).
 *
 * The body is sent empty on purpose: a solo (1v1) entrant has no team to name,
 * and the backend resolves the caller's own entry. `teamId` is only needed by a
 * captain of several teams.
 */
export async function lockIn(
  args: AuthedArgs & { tournamentId: string; teamId?: string },
  deps: HttpDeps = {},
): Promise<LockInResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  const payload: { team_id?: string } = {};
  if (args.teamId !== undefined && args.teamId !== "") {
    payload.team_id = args.teamId;
  }

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/check-in`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${args.accessToken}`,
        },
        body: JSON.stringify(payload),
      },
    );
  } catch (err) {
    return { ok: false, status: 0, detail: `Backend unreachable: ${String(err)}` };
  }

  if (res.ok) {
    return { ok: true };
  }

  let json: unknown;
  try {
    json = await res.json();
  } catch {
    json = undefined;
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not lock in (HTTP ${res.status}).`),
  };
}

// --- bracket (matches with team names) ------------------------------------- //

/** One match in a tournament bracket. */
export interface BracketMatch {
  readonly id: string;
  readonly round: number;
  readonly slot: number;
  readonly teamAName: string | null;
  readonly teamBName: string | null;
  readonly winnerTeamId: string | null;
  readonly teamAId: string | null;
  readonly teamBId: string | null;
  readonly status: string;
  /** ISO-8601 deadline for the 5-min ready window (null when not armed). */
  readonly readyDeadlineAt: string | null;
  readonly teamAReady: boolean;
  readonly teamBReady: boolean;
}

export type BracketResult =
  | { readonly ok: true; readonly tournament: Tournament; readonly matches: BracketMatch[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asBracketMatch(raw: unknown): BracketMatch {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (k: string): string | null => (typeof r[k] === "string" ? (r[k] as string) : null);
  const n = (k: string): number => (typeof r[k] === "number" ? (r[k] as number) : 0);
  const b = (k: string): boolean => r[k] === true;
  return {
    id: s("id") ?? "",
    round: n("round"),
    slot: n("slot"),
    teamAName: s("team_a_name"),
    teamBName: s("team_b_name"),
    winnerTeamId: s("winner_team_id"),
    teamAId: s("team_a_id"),
    teamBId: s("team_b_id"),
    status: s("status") ?? "pending",
    readyDeadlineAt: s("ready_deadline_at"),
    teamAReady: b("team_a_ready"),
    teamBReady: b("team_b_ready"),
  };
}

// --- ready-up (team captain marks their side ready) ------------------------ //

export type ReadyResult =
  | { readonly ok: true; readonly side: string; readonly bothReady: boolean }
  | { readonly ok: false; readonly status: number; readonly detail: string };

/** Mark the caller's team ready for a match (POST /matches/{id}/ready). */
export async function markReady(
  args: AuthedArgs & { matchId: string },
  deps: HttpDeps = {},
): Promise<ReadyResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/matches/${encodeURIComponent(args.matchId)}/ready`,
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
    const rec = (json ?? {}) as Record<string, unknown>;
    return {
      ok: true,
      side: typeof rec["side"] === "string" ? (rec["side"] as string) : "",
      bothReady: rec["both_ready"] === true,
    };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not ready up (HTTP ${res.status}).`),
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
