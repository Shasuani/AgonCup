// Human labels for the wire enums (libs/lol-schemas/src/lol_schemas/enums.py).
//
// The API speaks snake_case StrEnum values — `check_in`, `admin_override`,
// `summoners_rift_5v5`. Those are the CANONICAL values and they keep driving
// behaviour (status-badge colors key off the raw value via [data-status~="…"]),
// but they are not English and should never be the text a human reads. Every
// screen used to translate them ad hoc, or not at all; this is the one place
// that mapping lives.
//
// CONTRACT: `label()` never hides an unknown value. A status added server-side
// that we have not mapped yet falls back to `humanize()` — readable, and still
// visibly the real value — rather than an empty cell or a wrong guess.

/** Snake/kebab-case value → "Sentence case" fallback for unmapped enums. */
export function humanize(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  if (words === "") return value;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Look a value up in a label map, falling back to {@link humanize}. */
export function label(map: Readonly<Record<string, string>>, value: string): string {
  return map[value] ?? humanize(value);
}

/** `TournamentStatus`. */
export const TOURNAMENT_STATUS_LABELS: Readonly<Record<string, string>> = {
  draft: "Draft",
  open: "Registration open",
  check_in: "Check-in",
  running: "Running",
  completed: "Completed",
  cancelled: "Cancelled",
};

/** `MatchStatus`. */
export const MATCH_STATUS_LABELS: Readonly<Record<string, string>> = {
  pending: "Pending",
  scheduled: "Scheduled",
  in_progress: "In progress",
  completed: "Completed",
  walkover: "Walkover",
  forfeit: "Forfeit",
};

/** `MatchResultState`. */
export const RESULT_STATE_LABELS: Readonly<Record<string, string>> = {
  provisional: "Provisional",
  confirmed: "Confirmed",
  disputed: "Disputed",
};

/** `TournamentFormat`. */
export const FORMAT_LABELS: Readonly<Record<string, string>> = {
  single_elimination: "Single elimination",
  double_elimination: "Double elimination",
};

/** Compact `TournamentFormat` for table cells, where the full name wraps. */
export const FORMAT_LABELS_SHORT: Readonly<Record<string, string>> = {
  single_elimination: "Single elim",
  double_elimination: "Double elim",
};

/** `GameMode`. */
export const GAME_MODE_LABELS: Readonly<Record<string, string>> = {
  summoners_rift_5v5: "5v5 Summoner's Rift",
  aram_1v1: "1v1 ARAM",
};

/** Compact `GameMode` for table cells. */
export const GAME_MODE_LABELS_SHORT: Readonly<Record<string, string>> = {
  summoners_rift_5v5: "5v5 Rift",
  aram_1v1: "1v1 ARAM",
};

/** `ResultDecidedBy`. */
export const DECIDED_BY_LABELS: Readonly<Record<string, string>> = {
  quorum: "Quorum",
  admin_override: "Admin override",
  walkover: "Walkover",
  forfeit: "Forfeit",
};

/** `LobbyPhase`. */
export const LOBBY_PHASE_LABELS: Readonly<Record<string, string>> = {
  assigned_host: "Host assigned",
  awaiting_create: "Awaiting lobby",
  lobby_open: "Lobby open",
  filling: "Filling",
  ready: "Ready",
  starting: "Starting",
  in_game: "In game",
  result_pending: "Result pending",
  ended: "Ended",
  aborted: "Aborted",
  host_lost: "Host lost",
};

/** `TeamMemberRole`. `substitute` used to be a key here, aliasing the real `sub`
 *  so the old (invalid) client value still rendered. Nothing emits it now, and
 *  `humanize()` would produce the same "Substitute" anyway — keeping it only
 *  suggested the backend accepts that spelling. It does not. */
export const TEAM_ROLE_LABELS: Readonly<Record<string, string>> = {
  captain: "Captain",
  player: "Player",
  sub: "Substitute",
};

/** `UserGuildRoleName`. */
export const USER_ROLE_LABELS: Readonly<Record<string, string>> = {
  platform_operator: "Platform operator",
  guild_admin: "Guild admin",
  captain: "Captain",
  player: "Player",
};

/**
 * Riot platform routing values → the server name players recognise. Display
 * only: the raw code stays the visible text wherever it is shown as an
 * identifier, with this as the tooltip.
 */
export const REGION_LABELS: Readonly<Record<string, string>> = {
  br1: "Brazil",
  eun1: "EU Nordic & East",
  euw1: "EU West",
  jp1: "Japan",
  kr: "Korea",
  la1: "Latin America North",
  la2: "Latin America South",
  me1: "Middle East",
  na1: "North America",
  oc1: "Oceania",
  ph2: "Philippines",
  ru: "Russia",
  sg2: "Singapore",
  th2: "Thailand",
  tr1: "Türkiye",
  tw2: "Taiwan",
  vn2: "Vietnam",
};

/** Friendly server name for a stored region code, or undefined if unmapped. */
export function regionName(region: string | null | undefined): string | undefined {
  if (region === null || region === undefined) return undefined;
  return REGION_LABELS[region.toLowerCase()];
}

/**
 * Best-of N as a compact series label. `best_of` is 1 for a single game, so
 * "Bo1" reads oddly in prose — callers wanting a sentence use `seriesText`.
 */
export function seriesShort(bestOf: number): string {
  return `Bo${bestOf}`;
}

/** Best-of N spelled out, e.g. "Single game" / "Best of 3". */
export function seriesText(bestOf: number): string {
  return bestOf <= 1 ? "Single game" : `Best of ${bestOf}`;
}

/** "N teams" / "N players" depending on the tournament's game mode. */
export function entrantsText(count: number, gameMode: string): string {
  const noun = gameMode === "aram_1v1" ? "player" : "team";
  return `${count} ${count === 1 ? noun : `${noun}s`}`;
}
