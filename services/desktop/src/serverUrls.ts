/**
 * Which server an installed client talks to.
 *
 * Every backend call in this package falls back to `process.env.BACKEND_URL`
 * (the realtime transport to `REALTIME_URL`), and both default to localhost for
 * a developer running `electron .` against `make dev`. A player's installed
 * copy has no such env, so left alone it would quietly talk to a backend that
 * does not exist on their machine — every request failing with "can't reach the
 * server" on a client that was just downloaded from the production site.
 *
 * Resolved ONCE at boot and pure: an installed (packaged) build defaults to
 * production, a dev run to localhost, and an explicit env var wins in both
 * cases (that is how a client is pointed at a staging stack). The realtime and
 * downloads URLs derive from the backend origin unless overridden, so the three
 * can never disagree about which host they mean.
 */

/** The production site — the SPA, the API, and the installer downloads. */
export const PRODUCTION_ORIGIN = "https://lol.gameras.gr";

/** Where `electron .` looks when nothing says otherwise. */
export const DEV_ORIGIN = "http://localhost:8000";

/** The realtime endpoint path on the backend (PLAN.md §3.6). */
const REALTIME_PATH = "/api/v1/realtime/ws";

/** Where the site serves installers + the electron-updater feed (latest*.yml). */
const DOWNLOADS_PATH = "/downloads";

export interface ServerUrls {
  /** Backend origin, no trailing slash, e.g. `https://lol.gameras.gr`. */
  readonly backendUrl: string;
  /** Realtime WebSocket URL, e.g. `wss://lol.gameras.gr/api/v1/realtime/ws`. */
  readonly realtimeUrl: string;
  /** Installer directory, no trailing slash — the updater feed AND the .dmg's home. */
  readonly downloadsUrl: string;
}

function nonEmpty(value: string | undefined): string | undefined {
  return value !== undefined && value !== "" ? value : undefined;
}

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

/** `https://host` → `wss://host/api/v1/realtime/ws` (and http → ws). */
function realtimeFromBackend(backendUrl: string): string {
  const wsOrigin = backendUrl.replace(/^http(s?):/, "ws$1:");
  return `${wsOrigin}${REALTIME_PATH}`;
}

export function resolveServerUrls(args: {
  /** Electron's `app.isPackaged`: true for an installed build, false for `electron .`. */
  readonly isPackaged: boolean;
  readonly env: Readonly<Record<string, string | undefined>>;
}): ServerUrls {
  const backendUrl = stripTrailingSlash(
    nonEmpty(args.env["BACKEND_URL"]) ?? (args.isPackaged ? PRODUCTION_ORIGIN : DEV_ORIGIN),
  );
  const realtimeUrl = nonEmpty(args.env["REALTIME_URL"]) ?? realtimeFromBackend(backendUrl);
  return { backendUrl, realtimeUrl, downloadsUrl: `${backendUrl}${DOWNLOADS_PATH}` };
}
