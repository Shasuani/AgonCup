/**
 * Injected IO seams for the LCU adapter (PLAN.md §3.5 / §3.6).
 *
 * ============================================================================
 *  WHY INJECTION
 * ============================================================================
 *  The adapter must talk to two local, self-signed-TLS HTTPS surfaces:
 *    - the LCU (`https://127.0.0.1:<lockfilePort>`)            — RE, drift-prone
 *    - the Live Client Data API (`https://127.0.0.1:2999`)    — Documented
 *  and read the on-disk League `lockfile`. In a unit-test / non-League host
 *  none of that exists, so all three are expressed as INJECTED interfaces:
 *
 *    - {@link HttpTransport}     — a minimal request/response seam. The real
 *      wiring supplies a Node `https`/`undici` client configured to ACCEPT the
 *      LCU's self-signed loopback certificate (see the TLS note below); tests
 *      supply a fixture stub. No global `fetch` is assumed.
 *    - {@link FileSystemReader}  — reads the lockfile bytes + mtime (for
 *      rotation detection). Real wiring = `node:fs/promises`; tests = a stub.
 *    - {@link Clock} / {@link Sleeper} — virtualised time so the human-pacing /
 *      rate-cap logic (§3.6.3) is deterministically testable.
 *
 * ============================================================================
 *  SELF-SIGNED CERT HANDLING NOTE (do NOT globally disable TLS verification)
 * ============================================================================
 *  The LCU serves HTTPS on 127.0.0.1 with a per-install self-signed cert
 *  (Riot ships the well-known `riotgames.pem` root). The real
 *  {@link HttpTransport} implementation MUST scope cert handling to the
 *  loopback LCU origin only — e.g. a dedicated `https.Agent` whose CA is pinned
 *  to Riot's bundled root (preferred) or, as a last resort, an agent with
 *  `rejectUnauthorized:false` bound to THAT agent instance (never the process).
 *  This seam keeps that decision out of the adapter's audited core: the adapter
 *  never sees a socket, only this interface.
 */

/** HTTP verbs the adapter issues. LCU/EOG use GET + POST; :2999 is GET-only. */
export type HttpMethod = 'GET' | 'POST' | 'DELETE';

/** A single request the adapter asks the injected transport to perform. */
export interface HttpRequest {
  readonly method: HttpMethod;
  /** Fully-qualified URL (e.g. `https://127.0.0.1:54321/lol-lobby/v2/lobby`). */
  readonly url: string;
  /** Header map; the adapter sets `Authorization` + `Content-Type` as needed. */
  readonly headers: Readonly<Record<string, string>>;
  /** Pre-serialised JSON body for POSTs; omitted for GET/DELETE. */
  readonly body?: string;
}

/** The transport's response. `body` is the raw response text (often JSON). */
export interface HttpResponse {
  readonly status: number;
  readonly body: string;
}

/**
 * Minimal injected HTTP seam. The real implementation is responsible for the
 * self-signed-cert handling described in this file's header; the adapter only
 * ever sees status + text and is therefore transport-agnostic and testable.
 */
export interface HttpTransport {
  request(req: HttpRequest): Promise<HttpResponse>;
}

/** Lockfile bytes plus the mtime used to detect per-launch/patch rotation. */
export interface LockfileSnapshot {
  readonly contents: string;
  /** Epoch-ms mtime; a change signals the client relaunched (creds rotated). */
  readonly mtimeMs: number;
}

/** Injected filesystem seam (real = `node:fs/promises`; tests = a stub). */
export interface FileSystemReader {
  readLockfile(path: string): Promise<LockfileSnapshot>;
}

/** Injected monotonic clock (epoch-ms). Virtualised in tests. */
export interface Clock {
  nowMs(): number;
}

/** Injected sleep so pacing delays don't block real time in tests. */
export interface Sleeper {
  sleep(ms: number): Promise<void>;
}

/** Injected jitter source (0..1), so pacing jitter is deterministic in tests. */
export interface RandomSource {
  /** Uniform random in `[0, 1)`. */
  next(): number;
}

/** Everything the runnable adapter needs injected. */
export interface LcuRuntime {
  readonly http: HttpTransport;
  readonly fs: FileSystemReader;
  readonly clock: Clock;
  readonly sleeper: Sleeper;
  readonly random: RandomSource;
}
