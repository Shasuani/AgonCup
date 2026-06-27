/**
 * WebSocket {@link BackendTransport} for the desktop orchestration plane
 * (PLAN.md §3.6 / §5; WS_CONTRACT authoritative).
 *
 * This is the client half of the backend realtime endpoint
 * (`GET /api/v1/realtime/ws`). It:
 *
 *   - connects with the access JWT as a `?token=<jwt>` query param (the backend
 *     verifies + closes 4401 on failure before any data). NOTE: query-param
 *     tokens can leak via proxy / server logs — acceptable for the v1 native /
 *     loopback client; a `Sec-WebSocket-Protocol` header carrier is the
 *     documented follow-up (mirrors the backend hub's same note);
 *   - sends the first `{type:"subscribe",matchId}` frame on open and waits for
 *     `{type:"subscribed"}`; an `{type:"error"}` (or 4401 close) is surfaced;
 *   - parses inbound `{type:"command",command:<LobbyCommand>}` frames and fans
 *     them out to the registered `onCommand` handlers (the controller's seam);
 *   - serialises client->server `ack` / `heartbeat` / `report` frames per the
 *     wire contract;
 *   - RECONNECTS on any drop with bounded exponential backoff + jitter, and
 *     RE-SUBSCRIBES (re-sends the subscribe frame) on every reconnect.
 *
 * QUEUE / LAST-WRITE SEMANTICS (documented):
 *   - `sendAck` / `sendReport` are durable-ish: while disconnected they are
 *     QUEUED (FIFO) and flushed in order on the next successful subscribe. Acks
 *     and reports are corroboration-critical, so we do not drop them.
 *   - `sendHeartbeat` is LAST-WRITE-WINS: heartbeats are pure liveness and
 *     stale ones are worthless, so at most ONE pending heartbeat is retained
 *     while disconnected (a newer heartbeat overwrites the queued one). This
 *     bounds the queue and avoids a reconnect flushing a burst of dead beats.
 *   - The queue is bounded (`maxQueuedFrames`, default 256); on overflow the
 *     OLDEST queued ack/report is dropped (logged via `onWarn`) — the backend's
 *     corroboration quorum already tolerates an honest-missing report.
 *
 * The `WebSocket` is injected via {@link WebSocketFactory} so unit tests pass a
 * fake (no real socket is ever opened) and CONSTRUCTING this class performs NO
 * IO — `connect()` must be called explicitly. `@lol/lcu` symbols are imported
 * as TYPES ONLY so the compiled JS has no runtime adapter dependency.
 */

import type {
  BackendTransport,
  HostHeartbeat,
  LobbyAck,
  LobbyCommand,
  ResultReportMessage,
} from "../orchestration/protocol.js";

// --- injected socket seam --------------------------------------------------- //

/** WebSocket ready-state constants (mirrors the WHATWG / `ws` numeric values). */
export const WS_CONNECTING = 0 as const;
export const WS_OPEN = 1 as const;
export const WS_CLOSING = 2 as const;
export const WS_CLOSED = 3 as const;

/** Backend close code for auth failure (mirrors hub.WS_CLOSE_AUTH). */
export const WS_CLOSE_AUTH = 4401 as const;

/**
 * Minimal structural view of a `ws` WebSocket the transport drives. Only the
 * members used here are declared, so a test fake is tiny and the real `ws`
 * instance satisfies it structurally (no runtime import of `ws` in tests).
 */
export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  on(event: "open", listener: () => void): void;
  on(event: "message", listener: (data: unknown) => void): void;
  on(event: "close", listener: (code: number, reason?: unknown) => void): void;
  on(event: "error", listener: (err: unknown) => void): void;
}

/** Factory the transport calls to open a socket to `url`. Injected for tests. */
export type WebSocketFactory = (url: string) => WebSocketLike;

// --- backoff + queue config ------------------------------------------------- //

/** Bounded exponential-backoff-with-jitter parameters for reconnect. */
export interface ReconnectConfig {
  /** First retry delay (ms). */
  readonly baseDelayMs: number;
  /** Hard ceiling on the backoff delay (ms). */
  readonly maxDelayMs: number;
  /** Multiplier applied each consecutive failure. */
  readonly factor: number;
  /** Random jitter fraction in [0,1]; the delay is multiplied by 1±jitter/2. */
  readonly jitter: number;
}

const DEFAULT_RECONNECT: ReconnectConfig = {
  baseDelayMs: 500,
  maxDelayMs: 30_000,
  factor: 2,
  jitter: 0.5,
};

/** Injected timer seam (real impl wraps setTimeout; tests drive it manually). */
export interface Timers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const DEFAULT_TIMERS: Timers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export interface WsTransportConfig {
  /** Backend realtime base URL, e.g. `wss://host/api/v1/realtime/ws`. */
  readonly url: string;
  /** Access JWT, passed as `?token=` (see header note on leakage). */
  readonly token: string;
  /**
   * Optional live access-token source. When set it OVERRIDES {@link token} at
   * connect time, so a silent refresh (which rotates the access JWT) is picked
   * up automatically on the next (re)connect without rebuilding the transport.
   */
  readonly tokenProvider?: () => string;
  /** Match this connection subscribes to. */
  readonly matchId: string;
  /**
   * Guild the match belongs to (per-match WS authorization). Carried in the
   * subscribe frame so the backend sets the correct tenant GUC before the
   * participant check — a player can be in matches across guilds from ONE
   * identity-bearing session, so this is per-match, not baked into the token.
   * A forged guild fails closed server-side (zero rows => error frame + 4401).
   */
  readonly guildId?: string;
  readonly reconnect?: ReconnectConfig;
  /** Max queued ack/report frames before the oldest is dropped. */
  readonly maxQueuedFrames?: number;
  /** Optional structured-warning sink (drops, parse errors, reconnects). */
  readonly onWarn?: (event: string, detail: Record<string, unknown>) => void;
  /**
   * Invoked on an auth-rejection close (4401): the access token is bad/expired
   * and the transport has STOPPED retrying (a retry with the same token is
   * pointless). The app should silently refresh and then call {@link reconnect}.
   */
  readonly onAuthRejected?: () => void;
}

// --- frame shapes (wire contract) ------------------------------------------- //

/** Discriminated server->client frames. */
type ServerFrame =
  | { readonly type: "subscribed"; readonly matchId?: string }
  | { readonly type: "error"; readonly detail?: string }
  | { readonly type: "command"; readonly command: LobbyCommand };

/** Queued client->server payloads (already wrapped for the wire). */
type OutboundFrame =
  | { readonly type: "ack"; readonly ack: LobbyAck }
  | { readonly type: "report"; readonly report: ResultReportMessage }
  | { readonly type: "heartbeat"; readonly heartbeat: HostHeartbeat };

/**
 * WebSocket-backed {@link BackendTransport}. Construct it (no IO), register
 * `onCommand` handlers, then call {@link connect}; {@link close} for clean
 * shutdown (stops reconnecting).
 */
export class WsBackendTransport implements BackendTransport {
  readonly #config: WsTransportConfig;
  readonly #factory: WebSocketFactory;
  readonly #timers: Timers;
  readonly #reconnect: ReconnectConfig;
  readonly #maxQueued: number;
  readonly #handlers = new Set<(command: LobbyCommand) => void>();

  #socket: WebSocketLike | null = null;
  #subscribed = false;
  #closedByUser = false;
  #attempt = 0;
  #reconnectHandle: unknown = null;
  /** FIFO durable queue of acks/reports awaiting a live subscription. */
  #queue: OutboundFrame[] = [];
  /** Last-write-wins pending heartbeat (at most one). */
  #pendingHeartbeat: HostHeartbeat | null = null;

  constructor(config: WsTransportConfig, factory: WebSocketFactory, timers: Timers = DEFAULT_TIMERS) {
    this.#config = config;
    this.#factory = factory;
    this.#timers = timers;
    this.#reconnect = config.reconnect ?? DEFAULT_RECONNECT;
    this.#maxQueued = config.maxQueuedFrames ?? 256;
  }

  // --- BackendTransport surface -------------------------------------------- //

  onCommand(handler: (command: LobbyCommand) => void): () => void {
    this.#handlers.add(handler);
    return () => {
      this.#handlers.delete(handler);
    };
  }

  async sendAck(ack: LobbyAck): Promise<void> {
    this.#enqueueOrSend({ type: "ack", ack });
  }

  async sendReport(report: ResultReportMessage): Promise<void> {
    this.#enqueueOrSend({ type: "report", report });
  }

  async sendHeartbeat(heartbeat: HostHeartbeat): Promise<void> {
    if (this.#canSend()) {
      this.#rawSend({ type: "heartbeat", heartbeat });
      return;
    }
    // Last-write-wins: only the freshest heartbeat survives a disconnect.
    this.#pendingHeartbeat = heartbeat;
  }

  // --- lifecycle ----------------------------------------------------------- //

  /** Open the socket and begin the subscribe / reconnect loop. Idempotent. */
  connect(): void {
    this.#closedByUser = false;
    if (this.#socket !== null) {
      return;
    }
    this.#open();
  }

  /** Stop reconnecting and close the socket cleanly. */
  close(): void {
    this.#closedByUser = true;
    if (this.#reconnectHandle !== null) {
      this.#timers.clearTimeout(this.#reconnectHandle);
      this.#reconnectHandle = null;
    }
    const sock = this.#socket;
    this.#socket = null;
    this.#subscribed = false;
    sock?.close(1000, "client shutdown");
  }

  /** True once the backend has acknowledged the subscribe frame. */
  get isSubscribed(): boolean {
    return this.#subscribed;
  }

  // --- internals ----------------------------------------------------------- //

  #open(): void {
    const url = this.#connectUrl();
    let socket: WebSocketLike;
    try {
      socket = this.#factory(url);
    } catch (err) {
      this.#warn("connect_failed", { error: errorMessage(err) });
      this.#scheduleReconnect();
      return;
    }
    this.#socket = socket;
    this.#subscribed = false;

    socket.on("open", () => {
      // First frame MUST be the subscribe per WS_CONTRACT. The match's guild is
      // carried so the backend scopes the participant check to the right tenant.
      const guildId = this.#config.guildId;
      this.#rawSend({
        type: "subscribe",
        matchId: this.#config.matchId,
        ...(guildId !== undefined && guildId !== "" ? { guildId } : {}),
      });
    });
    socket.on("message", (data) => {
      this.#onMessage(data);
    });
    socket.on("error", (err) => {
      this.#warn("socket_error", { error: errorMessage(err) });
      // `close` follows an `error` on `ws`; reconnect is driven from there.
    });
    socket.on("close", (code) => {
      this.#onClose(code);
    });
  }

  /** Build the connect URL with the token query param (contract auth). */
  #connectUrl(): string {
    // Prefer the live provider (picks up a silently-refreshed access token).
    const token = this.#config.tokenProvider?.() ?? this.#config.token;
    const sep = this.#config.url.includes("?") ? "&" : "?";
    return `${this.#config.url}${sep}token=${encodeURIComponent(token)}`;
  }

  #onMessage(data: unknown): void {
    const frame = parseServerFrame(data);
    if (frame === null) {
      // Malformed / non-object / unknown frame: log + skip, never fatal.
      this.#warn("bad_frame", { raw: previewRaw(data) });
      return;
    }
    switch (frame.type) {
      case "subscribed":
        this.#subscribed = true;
        this.#attempt = 0; // a clean subscribe resets the backoff.
        this.#flushQueue();
        break;
      case "error":
        // Auth/scope failure: the backend closes after this; do not retry-storm.
        this.#warn("subscribe_error", { detail: frame.detail ?? "" });
        break;
      case "command":
        this.#dispatchCommand(frame.command);
        break;
    }
  }

  #dispatchCommand(command: LobbyCommand): void {
    for (const handler of this.#handlers) {
      try {
        handler(command);
      } catch (err) {
        // A handler must never break the read loop.
        this.#warn("handler_error", { error: errorMessage(err) });
      }
    }
  }

  #onClose(code: number): void {
    this.#socket = null;
    this.#subscribed = false;
    if (this.#closedByUser) {
      return;
    }
    if (code === WS_CLOSE_AUTH) {
      // Auth rejected: token is bad/expired. Retrying with the SAME token is
      // pointless; surface it and stop the loop. The app silently refreshes via
      // the hook and then calls reconnect() with the rotated token.
      this.#warn("auth_rejected", { code });
      this.#config.onAuthRejected?.();
      return;
    }
    this.#scheduleReconnect();
  }

  /**
   * Re-open after an auth-rejection close once the access token has been
   * refreshed (the {@link tokenProvider} now returns the fresh token). No-op if
   * the user closed the transport or a socket is already live / pending.
   */
  reconnect(): void {
    this.#closedByUser = false;
    if (this.#socket === null && this.#reconnectHandle === null) {
      this.#open();
    }
  }

  #scheduleReconnect(): void {
    if (this.#closedByUser || this.#reconnectHandle !== null) {
      return;
    }
    const delay = this.#backoffDelay(this.#attempt);
    this.#attempt += 1;
    this.#warn("reconnect_scheduled", { attempt: this.#attempt, delayMs: delay });
    this.#reconnectHandle = this.#timers.setTimeout(() => {
      this.#reconnectHandle = null;
      if (!this.#closedByUser) {
        this.#open();
      }
    }, delay);
  }

  /** Bounded exponential backoff with symmetric jitter. */
  #backoffDelay(attempt: number): number {
    const raw = this.#reconnect.baseDelayMs * this.#reconnect.factor ** attempt;
    const capped = Math.min(raw, this.#reconnect.maxDelayMs);
    const jitter = this.#reconnect.jitter;
    const factor = 1 + (Math.random() - 0.5) * jitter;
    return Math.max(0, Math.round(capped * factor));
  }

  #canSend(): boolean {
    return this.#socket !== null && this.#subscribed && this.#socket.readyState === WS_OPEN;
  }

  #enqueueOrSend(frame: OutboundFrame): void {
    if (this.#canSend()) {
      this.#rawSend(frame);
      return;
    }
    this.#queue.push(frame);
    if (this.#queue.length > this.#maxQueued) {
      const dropped = this.#queue.shift();
      this.#warn("queue_overflow", { dropped: dropped?.type ?? "unknown" });
    }
  }

  #flushQueue(): void {
    // Drain durable acks/reports in order, then the single pending heartbeat.
    const pending = this.#queue;
    this.#queue = [];
    for (const frame of pending) {
      this.#rawSend(frame);
    }
    if (this.#pendingHeartbeat !== null) {
      const hb = this.#pendingHeartbeat;
      this.#pendingHeartbeat = null;
      this.#rawSend({ type: "heartbeat", heartbeat: hb });
    }
  }

  #rawSend(frame: Record<string, unknown>): void {
    const sock = this.#socket;
    if (sock === null) {
      return;
    }
    try {
      sock.send(JSON.stringify(frame));
    } catch (err) {
      this.#warn("send_failed", { error: errorMessage(err), type: String(frame["type"]) });
    }
  }

  #warn(event: string, detail: Record<string, unknown>): void {
    this.#config.onWarn?.(event, detail);
  }
}

// --- pure parsing helpers --------------------------------------------------- //

/** Parse a raw inbound frame into a typed {@link ServerFrame}, or null if bad. */
function parseServerFrame(data: unknown): ServerFrame | null {
  let text: string;
  if (typeof data === "string") {
    text = data;
  } else if (data instanceof Uint8Array) {
    text = new TextDecoder().decode(data);
  } else if (typeof (data as { toString?: () => string })?.toString === "function") {
    text = String(data);
  } else {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) {
    return null;
  }
  const obj = parsed as Record<string, unknown>;
  switch (obj["type"]) {
    case "subscribed":
      return {
        type: "subscribed",
        ...(typeof obj["matchId"] === "string" ? { matchId: obj["matchId"] } : {}),
      };
    case "error":
      return {
        type: "error",
        ...(typeof obj["detail"] === "string" ? { detail: obj["detail"] } : {}),
      };
    case "command": {
      const command = obj["command"];
      if (typeof command !== "object" || command === null) {
        return null;
      }
      return { type: "command", command: command as LobbyCommand };
    }
    default:
      return null;
  }
}

/** A short, safe preview of a raw frame for diagnostics. */
function previewRaw(data: unknown): string {
  const s = typeof data === "string" ? data : String(data);
  return s.length > 120 ? `${s.slice(0, 120)}…` : s;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// --- real `ws` factory (lazy; never imported by unit tests) ----------------- //

/**
 * Build a {@link WebSocketFactory} backed by the `ws` package. Importing this
 * module does NOT import `ws`; the dynamic import happens only when the factory
 * is invoked (i.e. on a real `connect()` in the Electron main process), so unit
 * tests that never call it stay free of a `ws` runtime dependency.
 *
 * `ws` is loaded once and cached. The returned factory is synchronous (the
 * transport calls it synchronously), so the first call kicks off the import and
 * subsequent ones reuse the cached constructor; if the very first socket is
 * needed before the import settles the transport's reconnect loop covers it.
 */
export function createWsFactory(): WebSocketFactory {
  let Ctor: (new (url: string) => WebSocketLike) | null = null;
  let loading: Promise<void> | null = null;
  const ensure = (): Promise<void> => {
    if (loading === null) {
      loading = import("ws").then((mod) => {
        const resolved = (mod as { default?: unknown }).default ?? mod;
        Ctor = resolved as new (url: string) => WebSocketLike;
      });
    }
    return loading;
  };
  return (url: string): WebSocketLike => {
    if (Ctor === null) {
      // Kick off the load and throw so the transport schedules a reconnect; by
      // the retry the constructor is cached. (Real-process path only.)
      void ensure();
      throw new Error("ws module not yet loaded; will retry on reconnect");
    }
    return new Ctor(url);
  };
}
