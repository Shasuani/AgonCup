// Shared types + data access for the per-game match-history view (PLAN.md §3.6.5).
//
// Match history is the DISPLAY-ONLY scoreboard of games that have been played:
// for each game, the two teams, who won, and every player's champion + KDA. It is
// reconstructed on the backend from the (forgeable) end-of-game `result_reports`
// each desktop client posts, so it is the uncorroborated view — the OFFICIAL
// winner that advances the bracket still comes from adjudication / admin override,
// never this read.
//
// READ PATH: like the dispute queue, the typed client (lol-clients-ts) does not
// expose this list endpoint yet, so the read goes through the client's documented
// low-level `request` escape hatch at a stable path. The shapes below are the
// hand-authored contract; they move into the generated client when the backend
// ships its OpenAPI. All fields render as escaped React children — never raw HTML.

import type { ApiClient } from "@lol/clients-ts";

/** One scoreboard line: a player's champion + KDA, name resolved if linked. */
export interface HistoryPlayer {
  /** The (forgeable) eog PUUID. */
  puuid: string;
  /** Discord display name, resolved via the linked account (null if unlinked). */
  display_name?: string | null;
  /** "Name#TAG", resolved via the linked account (null if unlinked). */
  riot_id?: string | null;
  /** Numeric champion id (0 if absent); icon resolved from CommunityDragon. */
  champion_id: number;
  kills: number;
  deaths: number;
  assists: number;
}

/** One team's block within a game (winner flag + its players). */
export interface HistoryTeam {
  is_winner: boolean;
  players: HistoryPlayer[];
}

/** One played game, reconstructed from a representative result report. */
export interface HistoryGame {
  match_id: string;
  tournament_id: string;
  tournament_name?: string | null;
  round?: number | null;
  lcu_game_id: string;
  /** ISO-8601 timestamp the representative report was received. */
  played_at: string;
  teams: HistoryTeam[];
}

/** Read path under the API base (escape-hatch GET; see file header). */
export const MATCH_HISTORY_PATH = "/results/history";

/**
 * Fetch recent per-game scoreboards for the acting guild. Uses the client's
 * low-level request runner so it carries the same Bearer + X-Guild-Id headers as
 * every other tenant-scoped call.
 */
export function fetchMatchHistory(client: ApiClient): Promise<HistoryGame[]> {
  return client.request<HistoryGame[]>({
    method: "GET",
    path: MATCH_HISTORY_PATH,
  });
}

/** A champion square icon by numeric id (CommunityDragon, served by id). */
export function championIconUrl(championId: number): string {
  return (
    "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/" +
    `default/v1/champion-icons/${championId}.png`
  );
}

/** Best display label for a player: Discord name, else Riot ID, else short PUUID. */
export function playerLabel(player: HistoryPlayer): string {
  if (player.display_name) return player.display_name;
  if (player.riot_id) return player.riot_id;
  return player.puuid ? `${player.puuid.slice(0, 8)}…` : "Unknown";
}

/** Compact "k / d / a" string for a player. */
export function kdaText(player: HistoryPlayer): string {
  return `${player.kills} / ${player.deaths} / ${player.assists}`;
}
