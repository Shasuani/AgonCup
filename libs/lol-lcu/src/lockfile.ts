/**
 * Lockfile parsing + LCU auth derivation (own-client only) — PLAN.md §3.5.
 *
 * The League client writes a `lockfile` on launch with five colon-separated
 * fields: `name:pid:port:password:protocol`
 *   e.g. `LeagueClient:12345:54321:abcdEFGh:https`
 * The password rotates EVERY launch and on patch; callers re-read on rotation
 * (detected via mtime — see {@link LockfileSnapshot}). OWN-CLIENT ONLY: this is
 * the only credential the adapter is ever allowed to use; it never proxies or
 * drives another account.
 */

/** Parsed five-field lockfile record. */
export interface ParsedLockfile {
  readonly name: string;
  readonly pid: number;
  readonly port: number;
  readonly password: string;
  readonly protocol: 'https';
}

/**
 * Auth material derived from a parsed lockfile: the loopback base URL and the
 * pre-computed `Basic` header value for the fixed `riot:<password>` user.
 */
export interface LcuAuth {
  /** `https://127.0.0.1:<port>` — the loopback origin for all LCU calls. */
  readonly baseUrl: string;
  /** Ready-to-send header value, e.g. `Basic cmlvdDouLi4=`. */
  readonly authorizationHeader: string;
  readonly port: number;
  readonly password: string;
}

/** Thrown when the lockfile is malformed/unreadable so callers can fall back. */
export class LockfileParseError extends Error {
  override readonly name = 'LockfileParseError';
}

/**
 * Parse the raw `name:pid:port:password:protocol` lockfile line.
 *
 * Robust to a trailing newline and to passwords that themselves contain `:`
 * (we split on the FIRST three and LAST one separators, treating everything
 * in between as the password). Throws {@link LockfileParseError} on anything
 * that isn't a well-formed `https` lockfile.
 */
export function parseLockfile(contents: string): ParsedLockfile {
  const line = contents.replace(/\r?\n$/, '').trim();
  if (line.length === 0) {
    throw new LockfileParseError('lockfile is empty');
  }

  const parts = line.split(':');
  if (parts.length < 5) {
    throw new LockfileParseError(`lockfile has ${parts.length} fields, expected >= 5`);
  }

  // name:pid:port:<password possibly containing ':'>:protocol
  const name = parts[0]!;
  const pidRaw = parts[1]!;
  const portRaw = parts[2]!;
  const protocol = parts[parts.length - 1]!;
  const password = parts.slice(3, parts.length - 1).join(':');

  const pid = Number.parseInt(pidRaw, 10);
  const port = Number.parseInt(portRaw, 10);

  if (!Number.isInteger(pid) || pid <= 0) {
    throw new LockfileParseError(`invalid pid: ${pidRaw}`);
  }
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new LockfileParseError(`invalid port: ${portRaw}`);
  }
  if (protocol !== 'https') {
    throw new LockfileParseError(`unexpected protocol: ${protocol}`);
  }
  if (password.length === 0) {
    throw new LockfileParseError('empty password');
  }

  return { name, pid, port, password, protocol };
}

/**
 * Base64 helper that works in both Node (`Buffer`) and any environment exposing
 * `btoa`, so the adapter core stays runtime-agnostic and testable.
 */
function toBase64(value: string): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as unknown as {
    Buffer?: { from(s: string, enc: string): { toString(enc: string): string } };
    btoa?: (s: string) => string;
  };
  if (g.Buffer) {
    return g.Buffer.from(value, 'utf8').toString('base64');
  }
  if (g.btoa) {
    return g.btoa(value);
  }
  throw new LockfileParseError('no base64 encoder available in this runtime');
}

/**
 * Derive the loopback base URL + `Basic` auth header from a parsed lockfile.
 * The LCU always authenticates the fixed user `riot` with the lockfile
 * password. The HTTPS endpoint is self-signed at 127.0.0.1 — see the cert note
 * in `transport.ts` for how that is handled at the (injected) transport layer.
 */
export function deriveAuth(parsed: ParsedLockfile): LcuAuth {
  const baseUrl = `https://127.0.0.1:${parsed.port}`;
  const authorizationHeader = `Basic ${toBase64(`riot:${parsed.password}`)}`;
  return {
    baseUrl,
    authorizationHeader,
    port: parsed.port,
    password: parsed.password,
  };
}
