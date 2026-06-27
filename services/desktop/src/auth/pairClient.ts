/**
 * Desktop pairing + session-refresh backend client (main-process side).
 *
 * The desktop authenticates WITHOUT an OAuth consent screen: the Discord bot
 * mints a one-time pairing code (`/register`), the user pastes it here, and the
 * desktop exchanges it for OUR OWN session at `POST /api/v1/auth/desktop-pair`
 * (an access JWT + a rotating refresh token). Subsequent app launches refresh
 * silently at `POST /api/v1/auth/refresh`. The refresh token is the durable
 * credential — persisted encrypted via {@link SecureStore} (see secureStore.ts).
 *
 * Both calls NEVER throw — every failure (network, malformed code, expired code)
 * maps to a typed `{ ok:false, status, detail }` so the renderer can react
 * (re-prompt, drop to the pairing screen, etc).
 */

/** Minimal user summary returned by the pair endpoint for the UI to show state. */
export interface PairedUser {
  readonly userId: string;
  readonly discordUserId: string;
  readonly displayName: string;
}

/** Optional linked-account summary so the UI can skip step 2 if already linked. */
export interface PairedAccount {
  readonly linked: boolean;
  readonly puuid: string | null;
  readonly gameName: string | null;
  readonly tagLine: string | null;
  readonly region: string | null;
  readonly verified: boolean;
  readonly rankText: string;
}

/** Successful pairing / refresh: fresh access + (rotated) refresh tokens. */
export interface SessionTokens {
  readonly accessToken: string;
  readonly refreshToken: string;
}

export interface PairOk extends SessionTokens {
  readonly ok: true;
  readonly user: PairedUser;
  readonly account: PairedAccount | null;
}

export interface SessionErr {
  readonly ok: false;
  /** 0 => backend unreachable; otherwise the backend HTTP status. */
  readonly status: number;
  readonly detail: string;
}

export type PairResult = PairOk | SessionErr;

export interface RefreshOk extends SessionTokens {
  readonly ok: true;
}

export type RefreshResult = RefreshOk | SessionErr;

/** Injectable seams so these flows are unit-testable without Electron / network. */
export interface PairClientDeps {
  /** `fetch` implementation (defaults to global fetch). */
  readonly fetch?: typeof fetch;
  /** Backend base URL (defaults to BACKEND_URL env / localhost:8000). */
  readonly backendUrl?: string;
}

/** Resolve the backend base URL from explicit dep, env, or the dev default. */
function resolveBackendUrl(explicit?: string): string {
  if (explicit !== undefined && explicit !== "") {
    return explicit;
  }
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
    ?.env?.["BACKEND_URL"];
  return env !== undefined && env !== "" ? env : "http://localhost:8000";
}

/** Pull a string `detail` field out of a parsed JSON error body, if present. */
function detailOf(json: unknown, fallback: string): string {
  const d = (json as { detail?: unknown } | undefined)?.detail;
  return typeof d === "string" && d !== "" ? d : fallback;
}

/** Parse a response body as JSON, tolerating an empty / non-JSON body. */
async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

/** Map a successful token-pair body to {@link SessionTokens} (tokens required). */
function tokensFrom(json: unknown): SessionTokens | null {
  const rec = (json ?? {}) as { access_token?: unknown; refresh_token?: unknown };
  if (typeof rec.access_token === "string" && typeof rec.refresh_token === "string") {
    return { accessToken: rec.access_token, refreshToken: rec.refresh_token };
  }
  return null;
}

/** Shape the optional account summary from the pair response. */
function accountFrom(json: unknown): PairedAccount | null {
  const acc = (json as { account?: unknown } | undefined)?.account;
  if (acc === null || acc === undefined || typeof acc !== "object") {
    return null;
  }
  const rec = acc as Record<string, unknown>;
  return {
    linked: rec["linked"] === true,
    puuid: typeof rec["puuid"] === "string" ? rec["puuid"] : null,
    gameName: typeof rec["game_name"] === "string" ? rec["game_name"] : null,
    tagLine: typeof rec["tag_line"] === "string" ? rec["tag_line"] : null,
    region: typeof rec["region"] === "string" ? rec["region"] : null,
    verified: rec["verified"] === true,
    rankText: typeof rec["rank_text"] === "string" ? rec["rank_text"] : "Unranked",
  };
}

/**
 * Exchange a bot-minted pairing code for a session. POSTs
 * `{ code, device_label }` to `/api/v1/auth/desktop-pair`. Never throws.
 */
export async function pairDesktop(
  args: { code: string; deviceLabel?: string },
  deps: PairClientDeps = {},
): Promise<PairResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  const body = JSON.stringify({
    code: args.code,
    ...(args.deviceLabel !== undefined && args.deviceLabel !== ""
      ? { device_label: args.deviceLabel }
      : {}),
  });

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/auth/desktop-pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch (err) {
    return { ok: false, status: 0, detail: `Backend unreachable: ${String(err)}` };
  }

  const json = await readJson(res);
  if (!res.ok) {
    return { ok: false, status: res.status, detail: detailOf(json, `Pairing failed (HTTP ${res.status}).`) };
  }

  const tokens = tokensFrom(json);
  if (tokens === null) {
    return { ok: false, status: res.status, detail: "Pairing succeeded but the response was malformed." };
  }
  const userRec = (json as { user?: unknown }).user as Record<string, unknown> | undefined;
  const user: PairedUser = {
    userId: typeof userRec?.["user_id"] === "string" ? (userRec["user_id"] as string) : "",
    discordUserId:
      typeof userRec?.["discord_user_id"] === "string" ? (userRec["discord_user_id"] as string) : "",
    displayName:
      typeof userRec?.["display_name"] === "string" ? (userRec["display_name"] as string) : "",
  };
  return { ok: true, ...tokens, user, account: accountFrom(json) };
}

/**
 * Silently refresh the session: POSTs `{ refresh_token }` to
 * `/api/v1/auth/refresh`, returning the rotated token pair. Never throws. A
 * non-200 (typically 401 on an expired/revoked/reused token) maps to an error
 * so the caller can drop to the pairing screen.
 */
export async function refreshSession(
  refreshToken: string,
  deps: PairClientDeps = {},
): Promise<RefreshResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch (err) {
    return { ok: false, status: 0, detail: `Backend unreachable: ${String(err)}` };
  }

  const json = await readJson(res);
  if (!res.ok) {
    return { ok: false, status: res.status, detail: detailOf(json, `Refresh failed (HTTP ${res.status}).`) };
  }
  const tokens = tokensFrom(json);
  if (tokens === null) {
    return { ok: false, status: res.status, detail: "Refresh succeeded but the response was malformed." };
  }
  return { ok: true, ...tokens };
}
