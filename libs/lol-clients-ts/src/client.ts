/**
 * Typed API client factory. Groups the backend HTTP surface into resource
 * namespaces (auth, tournaments, teams, accounts, results, orchestration,
 * lifecycle, federation). Every method:
 *   - sets `Authorization: Bearer <token>` (from `getToken`) where relevant,
 *   - sets `X-Guild-Id` (from `getActingGuild`) on tenant-scoped routes,
 *   - parses JSON and returns a typed result,
 *   - throws {@link ApiError} on any non-2xx response.
 *
 * Framework-agnostic — no React, no global singletons. The shell wires
 * `getToken`/`getActingGuild` to its auth + acting-guild providers.
 */

import { createRequestRunner, type ClientConfig, type RequestRunner } from './http.js';
import type {
  AddMemberRequest,
  AdminGuild,
  AttemptStatusResponse,
  BracketResponse,
  CheckInRequest,
  CreatedTeamResponse,
  CreatedTournamentResponse,
  CreateTeamRequest,
  CreateTournamentRequest,
  EntrantResponse,
  FailoverResponse,
  FeedItemRefRequest,
  FeedItemResponse,
  HeartbeatRequest,
  IngestResponse,
  InitializeLobbyRequest,
  LinkAccountRequest,
  LinkAccountResponse,
  LobbyResponse,
  LobbyStateAckRequest,
  LogoutRequest,
  LogoutResponse,
  MatchResultResponse,
  PurgeResponse,
  RecordWinnerRequest,
  RefreshRequest,
  RegistrationResponse,
  RosterEntryResponse,
  ShareRequest,
  StartResponse,
  TeamMemberResponse,
  TeamRef,
  TeamResponse,
  TokenPair,
  TournamentResponse,
  TriggerFailoverRequest,
  UserDeletionResponse,
} from './types.js';

/** `encodeURIComponent` for a path segment (ids are interpolated into paths). */
function seg(value: string): string {
  return encodeURIComponent(value);
}

// ---------------------------------------------------------------------------
// Resource namespaces
// ---------------------------------------------------------------------------

/**
 * Auth routes. These do NOT attach the Bearer token (they issue/rotate it) and
 * are not tenant-scoped. `login` is a browser redirect, not an API call, so the
 * client only exposes its URL.
 */
export interface AuthApi {
  /** The `/auth/login` URL to navigate the browser to (Discord OAuth start). */
  loginUrl(): string;
  /** Rotate a refresh token → a fresh {@link TokenPair}. */
  refresh(body: RefreshRequest): Promise<TokenPair>;
  /** Revoke the presented refresh token. */
  logout(body: LogoutRequest): Promise<LogoutResponse>;
  /**
   * Discord servers the caller administers, each flagged with whether the bot is
   * already installed. Unlike the rest of this namespace it DOES require the
   * Bearer token (it reads the caller's stored Discord credential). A 409
   * `discord_reauth_required` means the user must sign in again.
   */
  adminGuilds(): Promise<AdminGuild[]>;
}

export interface TournamentsApi {
  /** The create response is the ONLY place the join code is returned. */
  create(body: CreateTournamentRequest): Promise<CreatedTournamentResponse>;
  list(): Promise<TournamentResponse[]>;
  get(tournamentId: string): Promise<TournamentResponse>;
  /** Who has entered, and who has locked in. */
  entrants(tournamentId: string): Promise<EntrantResponse[]>;
  /** The bracket: every match with team names (empty before Start). */
  bracket(tournamentId: string): Promise<BracketResponse>;
  register(tournamentId: string, body: TeamRef): Promise<RegistrationResponse>;
  /** Lock in. Omit `team_id` to lock in the caller's own entry. */
  checkIn(tournamentId: string, body?: CheckInRequest): Promise<RegistrationResponse>;
  start(tournamentId: string): Promise<StartResponse>;
  /**
   * Discard a DRAFT tournament. Rejects with 409 for anything past draft, which
   * holds other people's entries — that is a cancellation, not a deletion.
   */
  remove(tournamentId: string): Promise<void>;
}

export interface TeamsApi {
  /** Resolves with the join code — it is returned on create and nowhere else. */
  create(body: CreateTeamRequest): Promise<CreatedTeamResponse>;
  list(): Promise<TeamResponse[]>;
  get(teamId: string): Promise<TeamResponse>;
  listMembers(teamId: string): Promise<TeamMemberResponse[]>;
  addMember(teamId: string, body: AddMemberRequest): Promise<TeamMemberResponse>;
  removeMember(teamId: string, userId: string): Promise<void>;
  leave(teamId: string): Promise<void>;
}

export interface AccountsApi {
  /** ADMIN: list everyone who has linked a Riot account (player roster). */
  list(): Promise<RosterEntryResponse[]>;
  /** Link a Riot account + open an ownership challenge (camelCase body). */
  link(body: LinkAccountRequest): Promise<LinkAccountResponse>;
  /** Poll a verification attempt's status. */
  verifyStatus(attemptId: string): Promise<AttemptStatusResponse>;
}

export interface ResultsApi {
  /** Record a match's decided winner (admin override / dispute queue). */
  recordWinner(matchId: string, body: RecordWinnerRequest): Promise<MatchResultResponse>;
}

export interface OrchestrationApi {
  initializeLobby(matchId: string, body: InitializeLobbyRequest): Promise<LobbyResponse>;
  heartbeat(matchId: string, body: HeartbeatRequest): Promise<IngestResponse>;
  lobbyState(matchId: string, body: LobbyStateAckRequest): Promise<IngestResponse>;
  failover(matchId: string, body: TriggerFailoverRequest): Promise<FailoverResponse>;
}

export interface LifecycleApi {
  purgeGuild(guildId: string): Promise<PurgeResponse>;
  deleteUser(userId: string): Promise<UserDeletionResponse>;
}

/**
 * Federation routes are service-authenticated on the backend (the Discord bot
 * holds the token). Exposed here for the admin central-feed views; the feed is
 * read-only and the share actions carry the bot-asserted Discord context.
 */
export interface FederationApi {
  requestShare(body: ShareRequest): Promise<FeedItemResponse>;
  approveShare(body: FeedItemRefRequest): Promise<FeedItemResponse>;
  revokeShare(body: FeedItemRefRequest): Promise<FeedItemResponse>;
  listFeed(): Promise<FeedItemResponse[]>;
}

/** The full typed client returned by {@link createClient}. */
export interface ApiClient {
  readonly auth: AuthApi;
  readonly tournaments: TournamentsApi;
  readonly teams: TeamsApi;
  readonly accounts: AccountsApi;
  readonly results: ResultsApi;
  readonly orchestration: OrchestrationApi;
  readonly lifecycle: LifecycleApi;
  readonly federation: FederationApi;
  /** Escape hatch: the bound low-level request runner (advanced use). */
  readonly request: RequestRunner;
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * Build a typed API client.
 *
 * @example
 * const api = createClient({
 *   baseUrl: 'https://api.example.com',
 *   getToken: () => authStore.accessToken,
 *   getActingGuild: () => guildStore.actingGuildId,
 * });
 * const tournaments = await api.tournaments.list();
 */
export function createClient(config: ClientConfig): ApiClient {
  const request = createRequestRunner(config);
  const baseUrl = config.baseUrl.replace(/\/+$/, '');

  const auth: AuthApi = {
    loginUrl: () => `${baseUrl}/api/v1/auth/login`,
    refresh: (body) =>
      request<TokenPair>({ method: 'POST', path: '/auth/refresh', body, auth: false, tenant: false }),
    logout: (body) =>
      request<LogoutResponse>({ method: 'POST', path: '/auth/logout', body, auth: false, tenant: false }),
    // Authenticated, but deliberately NOT tenant-scoped: the whole point is to
    // answer "which servers can I install into?" before any guild is selected.
    adminGuilds: () => request<AdminGuild[]>({ method: 'GET', path: '/auth/guilds', tenant: false }),
  };

  const tournaments: TournamentsApi = {
    create: (body) =>
      request<CreatedTournamentResponse>({ method: 'POST', path: '/tournaments', body }),
    list: () => request<TournamentResponse[]>({ method: 'GET', path: '/tournaments' }),
    remove: (id) => request<void>({ method: 'DELETE', path: `/tournaments/${seg(id)}` }),
    get: (id) => request<TournamentResponse>({ method: 'GET', path: `/tournaments/${seg(id)}` }),
    entrants: (id) =>
      request<EntrantResponse[]>({ method: 'GET', path: `/tournaments/${seg(id)}/entrants` }),
    bracket: (id) =>
      request<BracketResponse>({ method: 'GET', path: `/tournaments/${seg(id)}/bracket` }),
    register: (id, body) =>
      request<RegistrationResponse>({
        method: 'POST',
        path: `/tournaments/${seg(id)}/registrations`,
        body,
      }),
    checkIn: (id, body) =>
      request<RegistrationResponse>({
        method: 'POST',
        path: `/tournaments/${seg(id)}/check-in`,
        body: body ?? {},
      }),
    start: (id) =>
      request<StartResponse>({ method: 'POST', path: `/tournaments/${seg(id)}/start` }),
  };

  const teams: TeamsApi = {
    create: (body) => request<CreatedTeamResponse>({ method: 'POST', path: '/teams', body }),
    list: () => request<TeamResponse[]>({ method: 'GET', path: '/teams' }),
    get: (id) => request<TeamResponse>({ method: 'GET', path: `/teams/${seg(id)}` }),
    listMembers: (id) =>
      request<TeamMemberResponse[]>({ method: 'GET', path: `/teams/${seg(id)}/members` }),
    addMember: (id, body) =>
      request<TeamMemberResponse>({ method: 'POST', path: `/teams/${seg(id)}/members`, body }),
    removeMember: (id, userId) =>
      request<void>({ method: 'DELETE', path: `/teams/${seg(id)}/members/${seg(userId)}` }),
    leave: (id) => request<void>({ method: 'POST', path: `/teams/${seg(id)}/leave` }),
  };

  const accounts: AccountsApi = {
    // Global (not guild-scoped) per the router — omit the tenant header.
    list: () =>
      request<RosterEntryResponse[]>({ method: 'GET', path: '/accounts', tenant: false }),
    link: (body) =>
      request<LinkAccountResponse>({ method: 'POST', path: '/accounts/link', body, tenant: false }),
    verifyStatus: (attemptId) =>
      request<AttemptStatusResponse>({
        method: 'GET',
        path: `/accounts/verify/${seg(attemptId)}/status`,
        tenant: false,
      }),
  };

  const results: ResultsApi = {
    recordWinner: (matchId, body) =>
      request<MatchResultResponse>({
        method: 'POST',
        path: `/results/matches/${seg(matchId)}/winner`,
        body,
      }),
  };

  const orchestration: OrchestrationApi = {
    initializeLobby: (matchId, body) =>
      request<LobbyResponse>({
        method: 'POST',
        path: `/orchestration/matches/${seg(matchId)}/lobby`,
        body,
      }),
    heartbeat: (matchId, body) =>
      request<IngestResponse>({
        method: 'POST',
        path: `/orchestration/matches/${seg(matchId)}/heartbeat`,
        body,
      }),
    lobbyState: (matchId, body) =>
      request<IngestResponse>({
        method: 'POST',
        path: `/orchestration/matches/${seg(matchId)}/lobby-state`,
        body,
      }),
    failover: (matchId, body) =>
      request<FailoverResponse>({
        method: 'POST',
        path: `/orchestration/matches/${seg(matchId)}/failover`,
        body,
      }),
  };

  const lifecycle: LifecycleApi = {
    purgeGuild: (guildId) =>
      request<PurgeResponse>({ method: 'POST', path: `/lifecycle/guilds/${seg(guildId)}/purge` }),
    deleteUser: (userId) =>
      request<UserDeletionResponse>({
        method: 'POST',
        path: `/lifecycle/users/${seg(userId)}/delete`,
        tenant: false,
      }),
  };

  const federation: FederationApi = {
    requestShare: (body) =>
      request<FeedItemResponse>({ method: 'POST', path: '/federation/share/request', body }),
    approveShare: (body) =>
      request<FeedItemResponse>({ method: 'POST', path: '/federation/share/approve', body }),
    revokeShare: (body) =>
      request<FeedItemResponse>({ method: 'POST', path: '/federation/share/revoke', body }),
    listFeed: () => request<FeedItemResponse[]>({ method: 'GET', path: '/federation/feed' }),
  };

  return {
    auth,
    tournaments,
    teams,
    accounts,
    results,
    orchestration,
    lifecycle,
    federation,
    request,
  };
}
