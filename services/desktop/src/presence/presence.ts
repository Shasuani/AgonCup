/**
 * GLOBAL live-presence read (`GET /api/v1/presence`).
 *
 * Powers the header bell badge (`players_in_game`) + panel: who is currently in
 * a game and which Discord server / tournament they belong to. Mirrors
 * {@link listMyAccounts}: injectable fetch/backendUrl, never throws, maps every
 * failure to a typed `{ ok:false, status, detail }` (status 0 = backend
 * unreachable), sends the access token as `Authorization: Bearer …`, and
 * camelises the snake_case body. A `401` is surfaced so the caller can refresh
 * the session and retry.
 */

/** One live match currently being played, as `/presence` returns it. */
export interface PresenceEntry {
  readonly matchId: string;
  readonly guildName: string;
  readonly tournamentName: string;
  readonly gameMode: string;
  readonly startedAt: string | null;
  readonly players: string[];
}

export type PresenceResult =
  | { readonly ok: true; readonly playersInGame: number; readonly entries: PresenceEntry[] }
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

function asPresenceEntry(raw: unknown): PresenceEntry {
  const rec = (raw ?? {}) as Record<string, unknown>;
  const players = Array.isArray(rec["players"])
    ? (rec["players"] as unknown[]).filter((p): p is string => typeof p === "string")
    : [];
  return {
    matchId: typeof rec["match_id"] === "string" ? rec["match_id"] : "",
    guildName: typeof rec["guild_name"] === "string" ? rec["guild_name"] : "",
    tournamentName: typeof rec["tournament_name"] === "string" ? rec["tournament_name"] : "",
    gameMode: typeof rec["game_mode"] === "string" ? rec["game_mode"] : "",
    startedAt: typeof rec["started_at"] === "string" ? rec["started_at"] : null,
    players,
  };
}

export async function getPresence(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<PresenceResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/presence`, {
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
    const rec = (json ?? {}) as Record<string, unknown>;
    const entriesRaw = Array.isArray(rec["entries"]) ? rec["entries"] : [];
    const entries = entriesRaw.map(asPresenceEntry);
    const playersInGame =
      typeof rec["players_in_game"] === "number" ? rec["players_in_game"] : 0;
    return { ok: true, playersInGame, entries };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load live presence (HTTP ${res.status}).`),
  };
}

/** One row of a live scoreboard — a display name, never a Riot ID. */
export interface LiveBoardPlayer {
  readonly name: string;
  /** Data Dragon champion key, e.g. `MissFortune`. */
  readonly championKey: string;
  readonly championName: string;
  /** `ORDER` (blue side) or `CHAOS` (red side). */
  readonly team: string;
  readonly level: number;
  readonly isDead: boolean;
  readonly respawnTimer: number;
  /** Item ids by slot 0..6 (0 = empty; 6 = trinket). */
  readonly items: number[];
  readonly kills: number;
  readonly deaths: number;
  readonly assists: number;
  readonly creepScore: number;
  readonly wardScore: number;
  readonly spell1Key: string | null;
  readonly spell2Key: string | null;
}

/** The latest live scoreboard of one game in progress. */
export interface LiveBoard {
  readonly matchId: string;
  /** Seconds since the game started. */
  readonly gameTime: number;
  readonly gameMode: string;
  readonly updatedAt: string;
  readonly players: LiveBoardPlayer[];
}

export type LiveBoardResult =
  | { readonly ok: true; readonly board: LiveBoard }
  | { readonly ok: false; readonly status: number; readonly detail: string };

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const optStr = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

function asLivePlayer(raw: unknown): LiveBoardPlayer {
  const rec = (raw ?? {}) as Record<string, unknown>;
  const items = Array.isArray(rec["items"]) ? (rec["items"] as unknown[]).map(num) : [];
  return {
    name: str(rec["name"]),
    championKey: str(rec["champion_key"]),
    championName: str(rec["champion_name"]),
    team: str(rec["team"]),
    level: num(rec["level"]),
    isDead: rec["is_dead"] === true,
    respawnTimer: num(rec["respawn_timer"]),
    items,
    kills: num(rec["kills"]),
    deaths: num(rec["deaths"]),
    assists: num(rec["assists"]),
    creepScore: num(rec["creep_score"]),
    wardScore: num(rec["ward_score"]),
    spell1Key: optStr(rec["spell1_key"]),
    spell2Key: optStr(rec["spell2_key"]),
  };
}

/**
 * The live scoreboard of a game in progress (`GET /api/v1/presence/live/{matchId}`).
 * Same audience as {@link getPresence}. `404` = not live, or no board sent yet.
 */
export async function getLiveBoard(
  args: AuthedArgs & { readonly matchId: string },
  deps: HttpDeps = {},
): Promise<LiveBoardResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/presence/live/${encodeURIComponent(args.matchId)}`, {
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
    const rec = (json ?? {}) as Record<string, unknown>;
    const players = Array.isArray(rec["players"]) ? rec["players"].map(asLivePlayer) : [];
    return {
      ok: true,
      board: {
        matchId: str(rec["match_id"]),
        gameTime: num(rec["game_time"]),
        gameMode: str(rec["game_mode"]),
        updatedAt: str(rec["updated_at"]),
        players,
      },
    };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the live scoreboard (HTTP ${res.status}).`),
  };
}
