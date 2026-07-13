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
