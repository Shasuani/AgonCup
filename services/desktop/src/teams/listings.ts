/**
 * Team listings — the "Find a team" screen (`/api/v1/team-listings/*`).
 *
 * A captain posts their team ("looking for players") and any player on ANY
 * Discord server running AgonCup can see it and ask to join. Same contract as
 * the rest of `teams.ts`: injectable fetch/backendUrl, never throw, every
 * failure is a typed `{ ok:false, status, detail }` (status 0 = backend
 * unreachable), Bearer auth, snake_case bodies camelised.
 */

import type { AuthedArgs, HttpDeps } from "./teams.js";

/** One active roster row as a stranger sees it (no Riot ID, ever). */
export interface ListingMember {
  readonly displayName: string;
  readonly role: string;
  /** Solo/Duo snapshot on the listing's server, e.g. "Gold II, 45 LP". */
  readonly rankText: string;
}

/** One open listing. */
export interface TeamListing {
  readonly id: string;
  readonly teamId: string;
  /** The team's server; a member sends it as `X-Guild-Id` on per-team calls. */
  readonly guildId: string;
  readonly teamName: string;
  readonly serverName: string;
  /** Riot platform route (`euw1`, `eun1`, …). */
  readonly region: string;
  readonly message: string | null;
  readonly createdAt: string;
  readonly memberCount: number;
  readonly maxMembers: number;
  readonly members: ListingMember[];
  /** The caller's tie to the team: "captain" / "member" / "pending", or null. */
  readonly viewerStatus: string | null;
  /** The caller may take it down (its captain, or a platform operator). */
  readonly canRemove: boolean;
}

type Failure = { readonly ok: false; readonly status: number; readonly detail: string };

export type ListListingsResult = { readonly ok: true; readonly listings: TeamListing[] } | Failure;
export type CreateListingResult = { readonly ok: true; readonly listingId: string } | Failure;
export type DeleteListingResult = { readonly ok: true } | Failure;
export type KeepAliveResult = { readonly ok: true; readonly kept: number } | Failure;
export type RequestJoinResult =
  | { readonly ok: true; readonly teamId: string; readonly teamName: string; readonly guildId: string; readonly requestStatus: string }
  | Failure;

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

function num(rec: Record<string, unknown>, key: string): number {
  return typeof rec[key] === "number" ? (rec[key] as number) : 0;
}

function asMember(raw: unknown): ListingMember {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    displayName: str(r, "display_name"),
    role: str(r, "role"),
    rankText: str(r, "rank_text") || "Unranked",
  };
}

function asListing(raw: unknown): TeamListing {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    id: str(r, "id"),
    teamId: str(r, "team_id"),
    guildId: str(r, "guild_id"),
    teamName: str(r, "team_name"),
    serverName: str(r, "server_name"),
    region: str(r, "region"),
    message: typeof r["message"] === "string" && r["message"] !== "" ? (r["message"] as string) : null,
    createdAt: str(r, "created_at"),
    memberCount: num(r, "member_count"),
    maxMembers: num(r, "max_members") || 5,
    members: Array.isArray(r["members"]) ? (r["members"] as unknown[]).map(asMember) : [],
    viewerStatus: typeof r["viewer_status"] === "string" ? (r["viewer_status"] as string) : null,
    canRemove: r["can_remove"] === true,
  };
}

/** One request; `body` undefined = no JSON body. Resolves to status + parsed JSON. */
async function call(
  method: string,
  path: string,
  args: AuthedArgs & { guildId?: string | undefined },
  deps: HttpDeps,
  body?: unknown,
): Promise<{ status: number; ok: boolean; json: unknown } | { status: 0; ok: false; json: undefined; error: string }> {
  const doFetch = deps.fetch ?? fetch;
  const headers: Record<string, string> = { Authorization: `Bearer ${args.accessToken}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (args.guildId !== undefined && args.guildId !== "") headers["X-Guild-Id"] = args.guildId;
  let res: Response;
  try {
    res = await doFetch(`${resolveBackendUrl(deps.backendUrl)}/api/v1/team-listings${path}`, {
      method,
      headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch (err) {
    return { status: 0, ok: false, json: undefined, error: `Backend unreachable: ${String(err)}` };
  }
  let json: unknown;
  try {
    json = res.status === 204 ? undefined : await res.json();
  } catch {
    json = undefined;
  }
  return { status: res.status, ok: res.ok, json };
}

function failure(r: { status: number; json: unknown; error?: string }, fallback: string): Failure {
  if (r.status === 0) return { ok: false, status: 0, detail: r.error ?? "Backend unreachable" };
  return { ok: false, status: r.status, detail: detailOf(r.json, `${fallback} (HTTP ${r.status}).`) };
}

/** Every open listing, newest first; `region` narrows to one server. */
export async function listListings(
  args: AuthedArgs & { region?: string | undefined },
  deps: HttpDeps = {},
): Promise<ListListingsResult> {
  const query = args.region ? `?region=${encodeURIComponent(args.region)}` : "";
  const r = await call("GET", query, args, deps);
  if (!r.ok) return failure(r, "Could not load the teams looking for players");
  return { ok: true, listings: Array.isArray(r.json) ? r.json.map(asListing) : [] };
}

/** Post the caller's team (captain). `guildId` = the team's server. */
export async function createListing(
  args: AuthedArgs & { teamId: string; guildId?: string | undefined; region: string; message?: string | undefined },
  deps: HttpDeps = {},
): Promise<CreateListingResult> {
  const message = args.message?.trim();
  const r = await call("POST", "", args, deps, {
    team_id: args.teamId,
    region: args.region,
    ...(message ? { message } : {}),
  });
  if (!r.ok) return failure(r, "Could not post your team");
  return { ok: true, listingId: str((r.json ?? {}) as Record<string, unknown>, "id") };
}

/** Take a listing down (its captain, or a platform operator). 204 on success. */
export async function deleteListing(
  args: AuthedArgs & { listingId: string },
  deps: HttpDeps = {},
): Promise<DeleteListingResult> {
  const r = await call("DELETE", `/${encodeURIComponent(args.listingId)}`, args, deps);
  if (!r.ok) return failure(r, "Could not delete the post");
  return { ok: true };
}

/** Ask to join a listed team — pending until its captain approves. */
export async function requestToJoin(
  args: AuthedArgs & { listingId: string },
  deps: HttpDeps = {},
): Promise<RequestJoinResult> {
  const r = await call("POST", `/${encodeURIComponent(args.listingId)}/request`, args, deps);
  if (!r.ok) return failure(r, "Could not send the request");
  const rec = (r.json ?? {}) as Record<string, unknown>;
  return {
    ok: true,
    teamId: str(rec, "team_id"),
    teamName: str(rec, "team_name"),
    guildId: str(rec, "guild_id"),
    requestStatus: str(rec, "status"),
  };
}

/**
 * "This client is still open": keeps the caller's posts on the list. The main
 * process sends it every minute while signed in; the backend drops a post it has
 * not heard about for 5 minutes, so closing the client takes the team down.
 */
export async function keepListingsAlive(
  args: AuthedArgs,
  deps: HttpDeps = {},
): Promise<KeepAliveResult> {
  const r = await call("POST", "/keep-alive", args, deps);
  if (!r.ok) return failure(r, "Could not keep your post up");
  return { ok: true, kept: num((r.json ?? {}) as Record<string, unknown>, "kept") };
}
