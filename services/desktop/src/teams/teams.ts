/**
 * Team reads/writes for the signed-in user (`/api/v1/teams/*`).
 *
 * `createTeam` (acting user becomes captain, response carries the shareable
 * `join_code`), `joinTeamByCode`, `getTeamJoinCode` (captain-only) and
 * `listTeamMembers` all mirror {@link listMyAccounts}: injectable
 * fetch/backendUrl, never throw, map every failure to a typed
 * `{ ok:false, status, detail }` (status 0 = backend unreachable), send the
 * access token as `Authorization: Bearer …`, and camelise the snake_case body.
 * A `401` is surfaced so the caller can refresh the session and retry.
 */

/** A team as `/teams` (create) returns it — includes the shareable join code. */
export interface CreatedTeam {
  readonly id: string;
  readonly name: string;
  readonly captainUserId: string;
  readonly guildId: string;
  readonly joinCode: string;
}

/** A team as `/teams/join-by-code` returns it (no join code echoed to joiners). */
export interface JoinedTeam {
  readonly id: string;
  readonly name: string;
  readonly captainUserId: string;
  readonly guildId: string;
}

/** One roster row from `/teams/{team_id}/members`. */
export interface TeamMember {
  readonly teamId: string;
  readonly userId: string;
  readonly role: string;
  /** The member's display name (falls back to the id if the backend omits it). */
  readonly displayName: string;
}

export interface AuthedArgs {
  /** Access JWT for the signed-in user (sent as `Authorization: Bearer …`). */
  readonly accessToken: string;
}

export interface HttpDeps {
  readonly fetch?: typeof fetch;
  readonly backendUrl?: string;
}

export type CreateTeamResult =
  | { readonly ok: true; readonly team: CreatedTeam }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type JoinTeamResult =
  | { readonly ok: true; readonly team: JoinedTeam }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type JoinCodeResult =
  | { readonly ok: true; readonly teamId: string; readonly joinCode: string }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type ListMembersResult =
  | { readonly ok: true; readonly members: TeamMember[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

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

function str(rec: Record<string, unknown>, key: string): string {
  return typeof rec[key] === "string" ? (rec[key] as string) : "";
}

function asCreatedTeam(raw: unknown): CreatedTeam {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    id: str(rec, "id"),
    name: str(rec, "name"),
    captainUserId: str(rec, "captain_user_id"),
    guildId: str(rec, "guild_id"),
    joinCode: str(rec, "join_code"),
  };
}

function asJoinedTeam(raw: unknown): JoinedTeam {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    id: str(rec, "id"),
    name: str(rec, "name"),
    captainUserId: str(rec, "captain_user_id"),
    guildId: str(rec, "guild_id"),
  };
}

function asTeamMember(raw: unknown): TeamMember {
  const rec = (raw ?? {}) as Record<string, unknown>;
  const userId = str(rec, "user_id");
  const name = str(rec, "display_name");
  return {
    teamId: str(rec, "team_id"),
    userId,
    role: str(rec, "role"),
    displayName: name !== "" ? name : userId,
  };
}

export async function createTeam(
  args: AuthedArgs & { name: string },
  deps: HttpDeps = {},
): Promise<CreateTeamResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${args.accessToken}`,
      },
      body: JSON.stringify({ name: args.name }),
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
    return { ok: true, team: asCreatedTeam(json) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not create the team (HTTP ${res.status}).`),
  };
}

export async function joinTeamByCode(
  args: AuthedArgs & { code: string },
  deps: HttpDeps = {},
): Promise<JoinTeamResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams/join-by-code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${args.accessToken}`,
      },
      body: JSON.stringify({ code: args.code }),
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
    return { ok: true, team: asJoinedTeam(json) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not join the team (HTTP ${res.status}).`),
  };
}

export async function getTeamJoinCode(
  args: AuthedArgs & { teamId: string },
  deps: HttpDeps = {},
): Promise<JoinCodeResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}/join-code`,
      {
        method: "GET",
        headers: { Authorization: `Bearer ${args.accessToken}` },
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
    return { ok: true, teamId: str(rec, "team_id"), joinCode: str(rec, "join_code") };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the join code (HTTP ${res.status}).`),
  };
}

export async function listTeamMembers(
  args: AuthedArgs & { teamId: string },
  deps: HttpDeps = {},
): Promise<ListMembersResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(
      `${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}/members`,
      {
        method: "GET",
        headers: { Authorization: `Bearer ${args.accessToken}` },
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
    return { ok: true, members: list.map(asTeamMember) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load the roster (HTTP ${res.status}).`),
  };
}

// --- team-leader approval flow (pending join requests) --------------------- //

/** One pending join request as `/teams/join-requests/*` returns it. */
export interface JoinRequest {
  readonly teamId: string;
  readonly teamName: string;
  readonly userId: string;
  readonly displayName: string;
}

export type ListJoinRequestsResult =
  | { readonly ok: true; readonly requests: JoinRequest[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export type ActOnRequestResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asJoinRequest(rec: unknown): JoinRequest {
  const r = (rec ?? {}) as Record<string, unknown>;
  return {
    teamId: str(r, "team_id"),
    teamName: str(r, "team_name"),
    userId: str(r, "user_id"),
    displayName: str(r, "display_name"),
  };
}

/** Pending join requests across every team the signed-in user captains. */
export async function listMyJoinRequests(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<ListJoinRequestsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams/join-requests/mine`, {
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
    return { ok: true, requests: list.map(asJoinRequest) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load join requests (HTTP ${res.status}).`),
  };
}

/** Approve (`approve`) or reject (`reject`) a pending request for one team. */
async function actOnRequest(
  action: "approve" | "reject",
  args: AuthedArgs & { teamId: string; userId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);
  const path =
    `${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}` +
    `/join-requests/${encodeURIComponent(args.userId)}/${action}`;

  let res: Response;
  try {
    res = await doFetch(path, {
      method: "POST",
      headers: { Authorization: `Bearer ${args.accessToken}` },
    });
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
    detail: detailOf(json, `Could not ${action} the request (HTTP ${res.status}).`),
  };
}

export function approveJoinRequest(
  args: AuthedArgs & { teamId: string; userId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  return actOnRequest("approve", args, deps);
}

export function rejectJoinRequest(
  args: AuthedArgs & { teamId: string; userId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  return actOnRequest("reject", args, deps);
}

/** Remove a member from a team (captain of that team). 204 on success. */
export async function removeMember(
  args: AuthedArgs & { teamId: string; userId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);
  const path =
    `${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}` +
    `/members/${encodeURIComponent(args.userId)}`;

  let res: Response;
  try {
    res = await doFetch(path, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${args.accessToken}` },
    });
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
    detail: detailOf(json, `Could not remove the member (HTTP ${res.status}).`),
  };
}

// --- "My Teams" (membership list + self-service leave) --------------------- //

/** A team the signed-in user belongs to, with their role + membership status. */
export interface MyTeam {
  readonly teamId: string;
  readonly name: string;
  readonly role: string;
  readonly status: string;
}

export type ListMyTeamsResult =
  | { readonly ok: true; readonly teams: MyTeam[] }
  | { readonly ok: false; readonly status: number; readonly detail: string };

function asMyTeam(raw: unknown): MyTeam {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    teamId: str(r, "team_id"),
    name: str(r, "name"),
    role: str(r, "role"),
    status: str(r, "status"),
  };
}

/** Teams the signed-in user is a member of (active or pending), with their role. */
export async function listMyTeams(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<ListMyTeamsResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams/mine`, {
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
    return { ok: true, teams: list.map(asMyTeam) };
  }
  return {
    ok: false,
    status: res.status,
    detail: detailOf(json, `Could not load your teams (HTTP ${res.status}).`),
  };
}

/** Leave a team (self-service; the captain must transfer/disband first → 409). */
export async function leaveTeam(
  args: AuthedArgs & { teamId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}/leave`, {
      method: "POST",
      headers: { Authorization: `Bearer ${args.accessToken}` },
    });
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
    detail: detailOf(json, `Could not leave the team (HTTP ${res.status}).`),
  };
}

/** Disband a team (captain of that team). 204 on success. */
export async function deleteTeam(
  args: AuthedArgs & { teamId: string },
  deps: HttpDeps = {},
): Promise<ActOnRequestResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/teams/${encodeURIComponent(args.teamId)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${args.accessToken}` },
    });
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
    detail: detailOf(json, `Could not disband the team (HTTP ${res.status}).`),
  };
}
