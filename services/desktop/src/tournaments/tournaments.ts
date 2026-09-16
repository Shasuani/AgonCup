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
  /** The Discord server the tournament belongs to — `/mine` and the join-code
   *  routes span every server the user is on, so every follow-up call about
   *  this row (entrants, lock-in, bracket, hide) sends it as `X-Guild-Id`. */
  readonly guildId: string;
  readonly name: string;
  readonly status: string;
  readonly format: string;
  readonly gameMode: string;
  readonly bestOf: number;
  readonly maxTeams: number;
  /** Riot platform it is played on ("euw1"/"eun1"/...); null = unrestricted.
   *  Joining needs a verified account on it — shown in the join preview so the
   *  player learns the requirement BEFORE redeeming, not from the error. */
  readonly region: string | null;
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

export type HideTournamentResult =
  | { readonly ok: true }
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

/** The tenant header for a per-object call.
 *
 * The backend pins a request to ONE Discord server: `X-Guild-Id` when sent,
 * else the server baked into the JWT (where the user holds their highest role).
 * The lists this client reads span every server the user belongs to, and each
 * row names its own `guildId`; a follow-up call about that row must carry it,
 * or a tournament on the friend's server is looked up on the user's own server
 * and reads as "not found". Empty/undefined sends nothing (the JWT default).
 */
function guildHeader(guildId: string | undefined): Record<string, string> {
  return guildId !== undefined && guildId !== "" ? { "X-Guild-Id": guildId } : {};
}

function asTournament(raw: unknown): Tournament {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    id: typeof rec["id"] === "string" ? rec["id"] : "",
    guildId: typeof rec["guild_id"] === "string" ? rec["guild_id"] : "",
    name: typeof rec["name"] === "string" ? rec["name"] : "",
    status: typeof rec["status"] === "string" ? rec["status"] : "",
    format: typeof rec["format"] === "string" ? rec["format"] : "",
    gameMode: typeof rec["game_mode"] === "string" ? rec["game_mode"] : "",
    bestOf: typeof rec["best_of"] === "number" ? rec["best_of"] : 1,
    maxTeams: typeof rec["max_teams"] === "number" ? rec["max_teams"] : 0,
    region: typeof rec["region"] === "string" && rec["region"] !== "" ? rec["region"] : null,
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

/** Clear a finished tournament off the caller's own list (DELETE /{id}/mine).
 *
 * Per-user, and NOT a delete: the tournament, its bracket and its results stay
 * exactly as they are for every other entrant and for the admin. The backend
 * accepts it only for a completed/cancelled tournament — hiding a live one would
 * take away the player's own route back to lock-in and the bracket — so a `409`
 * here means "that one is still running", not a bug.
 */
export async function hideTournament(
  args: AuthedArgs & { tournamentId: string; guildId?: string | undefined },
  deps: HttpDeps = {},
): Promise<HideTournamentResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/mine`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${args.accessToken}`, ...guildHeader(args.guildId) },
      },
    );
  } catch (err) {
    return { ok: false, status: 0, detail: `Backend unreachable: ${String(err)}` };
  }

  // 204 No Content: there is no body to parse on success.
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
    detail: detailOf(json, `Could not remove the tournament (HTTP ${res.status}).`),
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
/** A roster member of an entry, with their own lock-in state. */
export interface EntrantPlayer {
  readonly userId: string;
  readonly name: string;
  readonly lockedIn: boolean;
  readonly isYou: boolean;
}

export interface Entrant {
  readonly teamId: string;
  readonly name: string;
  /** The TEAM is ready — true only once every roster member has locked in. */
  readonly lockedIn: boolean;
  readonly isSolo: boolean;
  /** True for the caller's own entry (their solo team in 1v1, their team in 5v5). */
  readonly isYou: boolean;
  /** True while the caller still has to lock THEMSELVES in on this entry. */
  readonly canLockIn: boolean;
  /** The roster — what makes "waiting on two teammates" visible. */
  readonly players: EntrantPlayer[];
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
    players: Array.isArray(r["players"]) ? (r["players"] as unknown[]).map(asEntrantPlayer) : [],
  };
}

function asEntrantPlayer(raw: unknown): EntrantPlayer {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    userId: typeof r["user_id"] === "string" ? (r["user_id"] as string) : "",
    name: typeof r["name"] === "string" ? (r["name"] as string) : "",
    lockedIn: r["locked_in"] === true,
    isYou: r["is_you"] === true,
  };
}

/** Who has entered a tournament, and who has locked in (GET /{id}/entrants). */
export async function listEntrants(
  args: AuthedArgs & { tournamentId: string; guildId?: string | undefined },
  deps: HttpDeps = {},
): Promise<EntrantsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/entrants`,
      {
        method: "GET",
        headers: { Authorization: `Bearer ${args.accessToken}`, ...guildHeader(args.guildId) },
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
    const list = Array.isArray(json) ? json : [];
    return { ok: true, entrants: list.map(asEntrant) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the entrants (HTTP ${res.status}).`),
  };
}

/**
 * The account signed into the local League client, as read from the LCU
 * (READ-ONLY `current-summoner`). Sent with lock-in so the lobby invites THIS
 * account rather than whichever of the player's linked accounts the backend
 * would otherwise pick (an offline one, ToDoList #8). The Riot ID is the pair
 * the backend matches on; the LCU ids are extra hints for a renamed account.
 */
export interface ActiveAccount {
  readonly gameName: string;
  readonly tagLine: string;
  readonly region?: string | undefined;
  readonly lcuPuuid?: string | undefined;
  readonly summonerId?: string | undefined;
}

/** Wire shape of {@link ActiveAccount} (`active_account` on the check-in body). */
export interface ActiveAccountPayload {
  game_name: string;
  tag_line: string;
  region?: string;
  lcu_puuid?: string;
  summoner_id?: string;
}

function activeAccountPayload(account: ActiveAccount): ActiveAccountPayload {
  const payload: ActiveAccountPayload = {
    game_name: account.gameName,
    tag_line: account.tagLine,
  };
  if (account.region !== undefined && account.region !== "") {
    payload.region = account.region;
  }
  if (account.lcuPuuid !== undefined && account.lcuPuuid !== "") {
    payload.lcu_puuid = account.lcuPuuid;
  }
  if (account.summonerId !== undefined && account.summonerId !== "") {
    payload.summoner_id = account.summonerId;
  }
  return payload;
}

/** Lock in for a tournament (POST /{id}/check-in).
 *
 * A solo (1v1) entrant sends no `teamId` — the backend resolves the caller's
 * own entry; `teamId` is only needed by a captain of several teams. `hostOptIn`
 * carries the player's current "may my client run the lobby?" answer (the
 * automation toggle) so lock-in doubles as the hosting consent the orchestrator
 * seeds hosts from; omitted, any standing answer is left alone. `activeAccount`
 * names the account open in League right now (see {@link ActiveAccount}), so
 * the invite lands on it; omitted (League closed, never signed in), the backend
 * falls back to its own pick among the player's linked accounts.
 */
export async function lockIn(
  args: AuthedArgs & {
    tournamentId: string;
    teamId?: string;
    hostOptIn?: boolean;
    activeAccount?: ActiveAccount | undefined;
    guildId?: string | undefined;
  },
  deps: HttpDeps = {},
): Promise<LockInResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  const payload: {
    team_id?: string;
    host_opt_in?: boolean;
    active_account?: ActiveAccountPayload;
  } = {};
  if (args.teamId !== undefined && args.teamId !== "") {
    payload.team_id = args.teamId;
  }
  // Omitted (undefined) means "no new answer" — the backend leaves any standing
  // consent alone; an explicit true/false records the caller's current answer.
  if (args.hostOptIn !== undefined) {
    payload.host_opt_in = args.hostOptIn;
  }
  if (args.activeAccount !== undefined) {
    payload.active_account = activeAccountPayload(args.activeAccount);
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
          ...guildHeader(args.guildId),
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
  args: AuthedArgs & { matchId: string; guildId?: string | undefined },
  deps: HttpDeps = {},
): Promise<ReadyResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/matches/${encodeURIComponent(args.matchId)}/ready`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${args.accessToken}`, ...guildHeader(args.guildId) },
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
  args: AuthedArgs & { tournamentId: string; guildId?: string | undefined },
  deps: HttpDeps = {},
): Promise<BracketResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/tournaments/${encodeURIComponent(args.tournamentId)}/bracket`,
      {
        method: "GET",
        headers: { Authorization: `Bearer ${args.accessToken}`, ...guildHeader(args.guildId) },
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

// ---------------------------------------------------------------------------
// Live matches (orchestration)
// ---------------------------------------------------------------------------
/** One live match of the caller's, with whatever is known about its lobby. */
export interface MyMatch {
  readonly matchId: string;
  /** The match's guild — threaded into the WS subscribe frame for tenant scoping. */
  readonly guildId: string;
  readonly tournamentName: string;
  readonly roundLabel: string;
  /** Null until a lobby is initialized (drawn, but not opened yet). */
  readonly lobbyState: string | null;
  readonly lobbyName: string | null;
  readonly lobbyPassword: string | null;
  /** Null on the manual-fallback path — nobody's client is hosting. */
  readonly hostName: string | null;
  readonly isHost: boolean;
  /** `GameMode` value ("summoners_rift_5v5" / "aram_1v1") — drives the screen's
   * mode line and which win rule the live panel watches for. Defaults to 5v5
   * against an older backend that does not send it. */
  readonly gameMode: string;
}

export type ListMyMatchesResult =
  | { readonly ok: true; readonly matches: MyMatch[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asMyMatch(raw: unknown): MyMatch {
  const r = (raw ?? {}) as Record<string, unknown>;
  const str = (k: string): string => (typeof r[k] === "string" ? (r[k] as string) : "");
  const nullable = (k: string): string | null =>
    typeof r[k] === "string" && r[k] !== "" ? (r[k] as string) : null;
  return {
    matchId: str("match_id"),
    guildId: str("guild_id"),
    tournamentName: str("tournament_name"),
    roundLabel: str("round_label"),
    lobbyState: nullable("lobby_state"),
    lobbyName: nullable("lobby_name"),
    lobbyPassword: nullable("lobby_password"),
    hostName: nullable("host_name"),
    isHost: r["is_host"] === true,
    gameMode: nullable("game_mode") ?? "summoners_rift_5v5",
  };
}

/**
 * The caller's live matches (GET /orchestration/matches/mine).
 *
 * This is how the client learns a match exists at all. Until it did, the only way
 * to join a match's realtime channel was the `DESKTOP_MATCH_ID` environment
 * variable, so in a real tournament nobody subscribed and every orchestration
 * command the backend published was delivered to an empty channel.
 */
export async function listMyMatches(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<ListMyMatchesResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/orchestration/matches/mine`, {
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
    return { ok: true, matches: list.map(asMyMatch) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load your live matches (HTTP ${res.status}).`),
  };
}
