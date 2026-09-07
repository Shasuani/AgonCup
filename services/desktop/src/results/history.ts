/**
 * The signed-in player's recent games (`GET /api/v1/results/history/mine`).
 *
 * Powers the desktop "Match history" screen and the recent-games strip on Home.
 * Mirrors {@link listMyTournaments}: injectable fetch/backendUrl, never throws,
 * maps every failure to `{ ok:false, status, detail }` (status 0 = unreachable),
 * sends the access token as `Authorization: Bearer …`, and camelises the body.
 * A `401` is surfaced so the caller can refresh the session and retry.
 *
 * Display-only: these scoreboards are rebuilt from the players' own (forgeable)
 * end-of-game reports — the official winner still comes from adjudication.
 */

/** One scoreboard line. */
export interface HistoryPlayer {
  readonly puuid: string;
  readonly displayName: string | null;
  readonly riotId: string | null;
  readonly championId: number;
  readonly kills: number;
  readonly deaths: number;
  readonly assists: number;
  /** Scoreboard extras — null/empty on reports captured before they existed. */
  readonly championName: string | null;
  readonly level: number | null;
  /** Final build in slot order incl. trinket; 0 = empty slot. */
  readonly items: number[];
  readonly spell1Id: number | null;
  readonly spell2Id: number | null;
  readonly goldEarned: number | null;
  readonly minionsKilled: number | null;
  readonly damageToChampions: number | null;
  readonly visionScore: number | null;
  readonly profileIconId: number | null;
}

export interface HistoryTeam {
  readonly isWinner: boolean;
  readonly players: HistoryPlayer[];
}

export interface HistoryGame {
  readonly matchId: string;
  readonly tournamentId: string;
  readonly tournamentName: string | null;
  readonly round: number | null;
  readonly lcuGameId: string;
  /** ISO-8601 timestamp of the representative report. */
  readonly playedAt: string;
  readonly teams: HistoryTeam[];
}

export type ListMyHistoryResult =
  | { readonly ok: true; readonly games: HistoryGame[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface AuthedArgs {
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

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function optStr(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function asPlayer(raw: unknown): HistoryPlayer {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    puuid: str(r["puuid"]),
    displayName: optStr(r["display_name"]),
    riotId: optStr(r["riot_id"]),
    championId: num(r["champion_id"]),
    kills: num(r["kills"]),
    deaths: num(r["deaths"]),
    assists: num(r["assists"]),
    championName: optStr(r["champion_name"]),
    level: optNum(r["level"]),
    items: Array.isArray(r["items"]) ? (r["items"] as unknown[]).map(num) : [],
    spell1Id: optNum(r["spell1_id"]),
    spell2Id: optNum(r["spell2_id"]),
    goldEarned: optNum(r["gold_earned"]),
    minionsKilled: optNum(r["minions_killed"]),
    damageToChampions: optNum(r["damage_to_champions"]),
    visionScore: optNum(r["vision_score"]),
    profileIconId: optNum(r["profile_icon_id"]),
  };
}

function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function asTeam(raw: unknown): HistoryTeam {
  const r = (raw ?? {}) as Record<string, unknown>;
  const players = Array.isArray(r["players"]) ? r["players"] : [];
  return { isWinner: r["is_winner"] === true, players: players.map(asPlayer) };
}

export function asHistoryGame(raw: unknown): HistoryGame {
  const r = (raw ?? {}) as Record<string, unknown>;
  const teams = Array.isArray(r["teams"]) ? r["teams"] : [];
  return {
    matchId: str(r["match_id"]),
    tournamentId: str(r["tournament_id"]),
    tournamentName: optStr(r["tournament_name"]),
    round: typeof r["round"] === "number" ? r["round"] : null,
    lcuGameId: str(r["lcu_game_id"]),
    playedAt: str(r["played_at"]),
    teams: teams.map(asTeam),
  };
}

export async function listMyHistory(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<ListMyHistoryResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/results/history/mine`, {
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
    return { ok: true, games: list.map(asHistoryGame) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load your match history (HTTP ${res.status}).`),
  };
}
