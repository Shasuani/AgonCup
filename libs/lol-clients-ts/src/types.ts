/**
 * Request / response DTOs mirroring the FastAPI backend (services/backend).
 *
 * ============================================================================
 *  HAND-WRITTEN FOR NOW — see index.ts header.
 *  These interfaces mirror the Pydantic models in
 *  services/backend/src/app/<resource>/router.py. They are the hand-authored
 *  stand-in for the types that `make gen-clients` will later emit from the
 *  backend OpenAPI schema. Keep field names in lockstep with the routers
 *  (note: some bodies use camelCase aliases, e.g. accounts/link).
 * ============================================================================
 */

// ---------------------------------------------------------------------------
// auth — services/backend/src/app/auth/router.py
// ---------------------------------------------------------------------------

/**
 * Issued by `/auth/refresh`, and by `/auth/callback` — which does not return it
 * as a body but 303s to `<frontend>/auth/callback#access_token=..&refresh_token=..`
 * (parsed by the SPA's AuthCallback screen).
 */
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  /** Always "bearer". */
  token_type: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface LogoutRequest {
  refresh_token: string;
}

/** Response body of `POST /auth/logout`. */
export interface LogoutResponse {
  status: string;
}

/**
 * One row of `GET /auth/guilds` — a Discord server the caller administers, and
 * therefore a place they may install the bot.
 *
 * Ids are strings because Discord snowflakes exceed `Number.MAX_SAFE_INTEGER`
 * and would lose precision as JSON numbers.
 */
export interface AdminGuild {
  /** Discord snowflake of the server. */
  discord_guild_id: string;
  name: string;
  icon_url?: string | null;
  /** True when the caller owns the server (vs. merely holding ADMINISTRATOR). */
  owner: boolean;
  /** True when our bot is already in this server. */
  installed: boolean;
  /** Our internal guild UUID (the `X-Guild-Id` value). Null until installed. */
  guild_id?: string | null;
  /** Discord "add the bot" URL, preselected to this server. */
  invite_url: string;
}

/**
 * Decoded access-JWT claims. The backend mints these (app.auth.jwt); decode
 * client-side ONLY for the UI role gate — the backend still enforces authz.
 */
export interface AccessTokenClaims {
  /** Subject: the user id. */
  sub: string;
  /** Acting guild baked into the token at login/refresh (may be null). */
  guild_id?: string | null;
  /** Role values for the acting guild (e.g. "guild_admin", "captain"). */
  roles?: string[];
  /** Expiry (epoch seconds). */
  exp?: number;
  [claim: string]: unknown;
}

// ---------------------------------------------------------------------------
// tournaments — services/backend/src/app/tournaments/router.py
// ---------------------------------------------------------------------------

/** Mirrors lol_schemas.enums.TournamentFormat — the bracket engine implements
 *  these two only. (`round_robin`/`swiss` used to be listed here and are not
 *  backend values: picking one produced a 422 from an otherwise valid form.) */
export type TournamentFormat = 'single_elimination' | 'double_elimination';

/** Mirrors lol_schemas.enums.GameMode. Drives lobby shape AND enrolment shape:
 *  `aram_1v1` enrols individual players, `summoners_rift_5v5` enrols teams. */
export type GameMode = 'summoners_rift_5v5' | 'aram_1v1';

export interface CreateTournamentRequest {
  name: string;
  /** Defaults to single_elimination server-side when omitted. */
  format?: TournamentFormat;
  /** Defaults to summoners_rift_5v5 server-side when omitted. */
  game_mode?: GameMode;
  /** Series length per match: 1, 3 or 5. Defaults to 1 server-side. */
  best_of?: number;
  /** 2..16, defaults to 16 server-side. */
  max_teams?: number;
  ruleset?: Record<string, unknown>;
  /** ISO-8601 UTC. Enrolment is rejected before this instant. */
  registration_opens_at?: string;
  /** ISO-8601 UTC scheduled kickoff; enrolment closes once it passes. */
  start_at?: string;
}

export interface TournamentResponse {
  id: string;
  name: string;
  status: string;
  format: string;
  game_mode: string;
  best_of: number;
  max_teams: number;
  is_shared_to_central: boolean;
  registration_opens_at: string | null;
  start_at: string | null;
}

/** Create result — carries the join code, returned ONLY on create. */
export interface CreatedTournamentResponse extends TournamentResponse {
  join_code: string;
}

/** One entry in a tournament: a team in 5v5, a player (their solo team) in 1v1. */
export interface EntrantResponse {
  team_id: string;
  name: string;
  locked_in: boolean;
  is_solo: boolean;
  is_you: boolean;
  can_lock_in: boolean;
}

/** Lock-in body. `team_id` is optional: a solo entrant has no team to name. */
export interface CheckInRequest {
  team_id?: string;
}

/** Body for register / check-in. */
export interface TeamRef {
  team_id: string;
}

export interface RegistrationResponse {
  id: string;
  tournament_id: string;
  team_id: string;
  status: string;
}

export interface StartResponse {
  tournament_id: string;
  matches_created: number;
  rounds: number;
  byes: number;
}

// ---------------------------------------------------------------------------
// teams — services/backend/src/app/teams/router.py
// ---------------------------------------------------------------------------

export type TeamMemberRole = 'player' | 'substitute' | 'captain';

export interface CreateTeamRequest {
  name: string;
  captain_user_id: string;
}

export interface TeamResponse {
  id: string;
  name: string;
  captain_user_id: string;
  guild_id: string;
}

export interface AddMemberRequest {
  user_id: string;
  /** Defaults to "player" server-side when omitted. */
  role?: TeamMemberRole;
}

export interface TeamMemberResponse {
  team_id: string;
  user_id: string;
  role: string;
}

// ---------------------------------------------------------------------------
// accounts — services/backend/src/app/accounts/router.py
// NOTE: link body uses camelCase aliases (gameName/tagLine) per the router.
// ---------------------------------------------------------------------------

export interface LinkAccountRequest {
  gameName: string;
  tagLine: string;
  region: string;
}

export interface LinkAccountResponse {
  account_id: string;
  puuid: string;
  verified: boolean;
  attempt_id: string;
  target_icon_id: number;
  /** ISO-8601 datetime. */
  expires_at: string;
  game_name: string;
  tag_line: string;
}

export interface AttemptStatusResponse {
  attempt_id: string;
  puuid: string;
  status: string;
  target_icon_id: number;
  /** ISO-8601 datetime. */
  expires_at: string;
  verified: boolean;
}

/** One linked player in the admin roster (GET /accounts). */
export interface RosterEntryResponse {
  account_id: string;
  user_id: string;
  /** Owner's Discord display name. */
  discord_name: string;
  game_name: string | null;
  tag_line: string | null;
  region: string | null;
  verified: boolean;
  /** ISO-8601 datetime, or null if never verified. */
  verified_at: string | null;
  origin_guild_id: string | null;
  /** Name of the Discord server the user ran /register from. */
  origin_guild_name: string | null;
}

// ---------------------------------------------------------------------------
// results — services/backend/src/app/results/router.py
// The dispute/override queue's decided-winner endpoint.
// ---------------------------------------------------------------------------

export type ResultDecidedBy =
  | 'admin_override'
  | 'quorum'
  | 'walkover'
  | 'forfeit';

export interface RecordWinnerRequest {
  winner_team_id: string;
  /** Defaults to "admin_override" server-side when omitted. */
  decided_by?: ResultDecidedBy;
  /** Optional override reason (max 500 chars). */
  reason?: string | null;
}

export interface MatchResultResponse {
  match_id: string;
  tournament_id: string;
  winner_team_id: string;
  decided_by: string;
  state: string;
}

// ---------------------------------------------------------------------------
// orchestration — services/backend/src/app/orchestration/router.py
// ---------------------------------------------------------------------------

export type LobbyPhase =
  | 'pending'
  | 'creating'
  | 'lobby_open'
  | 'champ_select'
  | 'in_game'
  | 'completed'
  | 'aborted';

export type LobbyEvent =
  | 'lobby_created'
  | 'players_joined'
  | 'champ_select_started'
  | 'game_started'
  | 'game_completed'
  | 'aborted';

export interface InitializeLobbyRequest {
  lobby_name: string;
  lobby_password: string;
}

export interface LobbyResponse {
  lobby_id: string;
  match_id: string;
  host_puuid: string;
  host_epoch: number;
  succession: string[];
  state: string;
}

export interface HeartbeatRequest {
  host_epoch: number;
  phase: LobbyPhase;
}

export interface LobbyStateAckRequest {
  host_epoch: number;
  event: LobbyEvent;
  lcu_lobby_id?: string | null;
}

export interface IngestResponse {
  accepted: boolean;
  host_epoch: number;
  state: string;
  detail: string;
}

export interface TriggerFailoverRequest {
  host_connected?: boolean;
  missed_heartbeats: number;
  gameflow_advanced?: boolean;
}

export interface FailoverResponse {
  action: string;
  host_epoch: number;
  host_puuid: string | null;
  state: string;
  reason: string;
}

// ---------------------------------------------------------------------------
// lifecycle — services/backend/src/app/lifecycle/router.py
// ---------------------------------------------------------------------------

export interface PurgeResponse {
  guild_id: string;
  status: string;
  /** ISO-8601 datetime (empty string for an unknown-guild noop). */
  removed_at: string;
  deleted_rows: Record<string, number>;
  total_deleted: number;
}

export interface UserDeletionResponse {
  user_id: string;
  deleted: boolean;
}

// ---------------------------------------------------------------------------
// federation — services/backend/src/app/federation/router.py
// Service-authenticated (the bot holds the token); shapes exported for the
// admin central-feed views (PLAN.md §3.2).
// ---------------------------------------------------------------------------

/** The bot-asserted Discord interaction context. */
export interface DiscordContextIn {
  discord_user_id: number;
  discord_guild_id: number;
  is_discord_guild_admin?: boolean;
  display_name?: string | null;
}

export interface ShareRequest {
  ctx: DiscordContextIn;
  tournament_id: string;
}

export interface FeedItemRefRequest {
  ctx: DiscordContextIn;
  item_id: string;
}

export interface FeedItemResponse {
  id: string;
  source_guild_id: string;
  tournament_id: string;
  projected_fields: Record<string, string>;
  status: string;
  published_at: string | null;
  approved_at: string | null;
  revoked_at: string | null;
}
