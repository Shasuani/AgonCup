/**
 * Ask the backend whether this client is current (`GET /api/v1/desktop/version`).
 *
 * The backend reads the same electron-builder feed the installers ship with, so
 * this is the one question the client has to ask; it never throws and maps every
 * failure to `{ ok:false, status, detail }` (status 0 = unreachable), like the
 * other HTTP modules in this package. Unauthenticated: it runs before pairing.
 */

export type ReleaseStatus = "update_available" | "up_to_date" | "unknown";

/** The backend's answer, camelised. */
export interface ReleaseInfo {
  readonly platform: string;
  readonly current: string | null;
  readonly latest: string | null;
  readonly minimum: string | null;
  readonly status: ReleaseStatus;
  readonly upToDate: boolean;
  /** Below the server's minimum: the client hides "Later". */
  readonly required: boolean;
  readonly publishedAt: string | null;
  /** The installer a person can run by hand (the site's stable .dmg / .exe). */
  readonly downloadUrl: string;
  /** What the in-app updater fetches (Windows: the NSIS .exe; macOS: the zip). */
  readonly packageUrl: string | null;
  /** Base64 SHA-512 of `packageUrl`, from the feed. */
  readonly packageSha512: string | null;
  readonly packageSize: number | null;
  readonly feedUrl: string;
}

export type VersionCheckResult =
  | { readonly ok: true; readonly release: ReleaseInfo }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface HttpDeps {
  readonly fetch?: typeof fetch;
  readonly backendUrl?: string;
}

function resolveBackendUrl(explicit?: string): string {
  if (explicit !== undefined && explicit !== "") return explicit;
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
    ?.env?.["BACKEND_URL"];
  return env !== undefined && env !== "" ? env : "http://localhost:8000";
}

function optStr(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function asReleaseInfo(raw: unknown): ReleaseInfo {
  const r = (raw ?? {}) as Record<string, unknown>;
  const status = r["status"];
  return {
    platform: optStr(r["platform"]) ?? "",
    current: optStr(r["current"]),
    latest: optStr(r["latest"]),
    minimum: optStr(r["minimum"]),
    status:
      status === "update_available" || status === "up_to_date" ? status : "unknown",
    upToDate: r["up_to_date"] !== false,
    required: r["required"] === true,
    publishedAt: optStr(r["published_at"]),
    downloadUrl: optStr(r["download_url"]) ?? "",
    packageUrl: optStr(r["package_url"]),
    packageSha512: optStr(r["package_sha512"]),
    packageSize: optNum(r["package_size"]),
    feedUrl: optStr(r["feed_url"]) ?? "",
  };
}

export async function checkDesktopVersion(
  args: { readonly currentVersion: string; readonly platform: string },
  deps: HttpDeps = {},
): Promise<VersionCheckResult> {
  const doFetch = deps.fetch ?? fetch;
  const backendUrl = resolveBackendUrl(deps.backendUrl);
  // The backend knows two feeds; a dev box on Linux still wants "what is latest".
  const platform = args.platform === "darwin" ? "darwin" : "win32";
  const query = new URLSearchParams({ platform, version: args.currentVersion });

  let res: Response;
  try {
    res = await doFetch(`${backendUrl}/api/v1/desktop/version?${query.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
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

  if (res.ok) return { ok: true, release: asReleaseInfo(json) };
  const d = (json as { detail?: unknown } | undefined)?.detail;
  return {
    ok: false,
    status: res.status,
    detail: typeof d === "string" && d !== "" ? d : `Version check failed (HTTP ${res.status}).`,
  };
}
