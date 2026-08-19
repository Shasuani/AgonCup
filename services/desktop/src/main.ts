/**
 * LeagueOfLeagues desktop client — Electron main process (PLAN.md §3.3 / §3.6).
 *
 * The desktop client is **load-bearing for match EXECUTION** but **advisory for
 * result TRUTH** (PLAN.md §0 split trust model). It:
 *   - autonomously drives the local League client over the LCU (host: create
 *     lobby, invite by PUUID; guest: auto-accept) via the single audited
 *     `@lol/lcu` adapter — the one fix-point for reverse-engineered, drifting
 *     LCU calls;
 *   - captures live state (`:2999`) and end-of-game stats, and reports them to
 *     the backend, where they are corroborated across all 10 clients.
 *
 * AUTH posture (docs/desktop-auth-plan.md): the trust anchor is the Discord bot,
 * not OAuth. The user pastes a one-time pairing code (minted by `/register`),
 * the desktop exchanges it for OUR OWN session (access JWT + rotating refresh
 * token), the refresh token is persisted ENCRYPTED via {@link SecureStore}, and
 * every reopen refreshes silently. Account-linking (step 2) is an AUTHENTICATED
 * `verify-lcu` call gated on the LCU being online.
 *
 * ToS posture (PLAN.md §3.3 / §9): every LCU **write** is gated behind a
 * per-session, default-OFF opt-in + presence gate + human-paced rate limits +
 * a recorded consent + a global kill-switch. Korea is excluded. No in-match
 * gameplay automation, ever.
 *
 * IMPORT-SAFETY (load-bearing for tests): importing this module performs NO IO.
 * It must not open a WebSocket, read the lockfile, persist a file, or create an
 * Electron window. All of that lives behind `bootstrap()`, invoked only from the
 * real entrypoint guarded by `isMainEntry()`. Constructors are pure.
 */

import {
  LcuAdapterImpl,
  type LcuAdapter,
  type LcuAdapterConfig,
  type LcuSchemaVersion,
  type Puuid,
} from "@lol/lcu";
import { createRealLcuRuntime } from "./lcu/realRuntime.js";
import {
  OrchestrationController,
  type Clock,
  type Scheduler,
} from "./orchestration/index.js";
import { WsBackendTransport, createWsFactory, type WebSocketFactory } from "./transport/wsTransport.js";
import { checkLcuIdentity, type IdentityCheckResult } from "./identity/identityCheck.js";
import { readCurrentSummoner } from "./lcu/readSummoner.js";
import { verifyDesktop, type VerifyResult } from "./lcu/verify.js";
import { listMyAccounts, type ListAccountsResult } from "./accounts/listAccounts.js";
import { unlinkAccount } from "./accounts/unlinkAccount.js";
import {
  getBracket,
  hideTournament,
  joinTournamentByCode,
  listEntrants,
  listMyMatches,
  listMyTournaments,
  lookupTournamentByCode,
  lockIn,
  markReady,
  type MyMatch,
} from "./tournaments/tournaments.js";
import {
  approveJoinRequest,
  createTeam,
  deleteTeam,
  getTeamJoinCode,
  joinTeamByCode,
  leaveTeam,
  listMyJoinRequests,
  listMyTeams,
  listTeamMembers,
  rejectJoinRequest,
  removeMember,
} from "./teams/teams.js";
import { getPresence } from "./presence/presence.js";
import {
  pairDesktop,
  refreshSession,
  type PairedUser,
  type PairResult,
  type RefreshResult,
} from "./auth/pairClient.js";
import { SecureStore, type PersistedSession } from "./secureStore.js";
import { SettingsStore } from "./settingsStore.js";
import { LcuStatusPoller, type LcuStatus } from "./lcu/statusPoller.js";

/** Default heartbeat cadence while pre-game/in-game (PLAN.md §3.6.1 ~2-3s). */
const DEFAULT_HEARTBEAT_MS = 2_500;

/**
 * How often the client asks which matches are its own.
 *
 * Deliberately slow: this only has to notice that a bracket started, which is a
 * human-scale event. Everything time-critical after that arrives on the realtime
 * channel this poll's answer lets us subscribe to.
 */
const MATCH_POLL_MS = 15_000;

/** How long to wait before re-opening sockets a 4401 closed while the backend
 * was unreachable. Long enough that a restarting backend is usually back, short
 * enough that a live match reconnects without the player noticing. Exported so
 * a test can tell this timer apart from the controller's heartbeat, which shares
 * the injected scheduler. */
export const AUTH_RETRY_MS = 10_000;

/**
 * Build the LCU adapter with writes disabled by default (opt-in per session).
 *
 * This used to hand back the {@link LcuAdapterSkeleton}, whose every method
 * throws "not implemented". The runnable adapter had been written and was wired
 * into exactly one place — the read-only current-summoner probe — so the client
 * dutifully subscribed to its match, received every command, and could not carry
 * out a single one: create, invite and auto-accept all failed identically at the
 * first line, and the match sat there with nobody able to say why.
 *
 * Constructing it is still pure (no lockfile read, no socket): the runtime only
 * touches the disk when a call is made.
 */
export function createLcuAdapter(): LcuAdapter {
  const config: LcuAdapterConfig = {
    // Writes are OFF until the player explicitly opts in this session, and stay
    // off while the kill-switch is engaged or the player is AFK (PLAN.md §3.3).
    writeAutomationOptIn: false,
    killSwitchEngaged: false,
    requirePresenceForWrites: true,
    pacing: {
      minWriteDelayMs: 750,
      writeJitterMs: 500,
      maxWritesPerMinute: 20,
      maxBackoffMs: 30_000,
      maxRetryAttempts: 3,
    },
    // Pinned, fixture-validated client patch (set by the build; placeholder here).
    schemaVersion: "0.0.0-phase0" as LcuSchemaVersion,
  };
  return new LcuAdapterImpl({ config, runtime: createRealLcuRuntime() });
}

// --- real runtime seams (wall clock + setInterval scheduler) ---------------- //

/** Wall clock for heartbeat timestamps. */
const SYSTEM_CLOCK: Clock = { nowMs: () => Date.now() };

/** setInterval-backed scheduler (the controller's injected periodic seam). */
const SYSTEM_SCHEDULER: Scheduler = {
  every(ms, fn) {
    const handle = setInterval(fn, ms);
    return () => clearInterval(handle);
  },
};

// --- session (token source of truth) ---------------------------------------- //

/** The store surface {@link DesktopSession} needs to persist rotated tokens. */
export interface SessionStore {
  save(session: PersistedSession): Promise<void>;
  clear(): Promise<void>;
}

/**
 * Why a silent refresh produced no session.
 *
 * The distinction is the entire point of this type. ONLY the server can declare
 * the stored credential dead, and it says so with a 401. Everything else — the
 * backend restarting, a dropped wifi link, a 429, a 502 from the proxy — means
 * "ask again later". Collapsing the two is what made a client that was offline
 * for ten seconds throw away a perfectly good refresh token and demand a fresh
 * pairing code from the bot.
 */
export type RefreshFailure = "expired" | "unavailable";

/** Outcome of a silent refresh: rotated, or why not. */
export type RefreshOutcome =
  | { readonly ok: true }
  | { readonly ok: false; readonly reason: RefreshFailure };

/** What {@link DesktopRuntime}'s silent-refresh hook resolves with. */
export type RefreshAuthResult =
  | { readonly ok: true; readonly token: string }
  | { readonly ok: false; readonly reason: RefreshFailure };

/**
 * Classify a `/auth/refresh` failure status.
 *
 * `status: 0` is this client's own "never reached the backend"; 429/5xx are the
 * backend declining to answer right now; only 401 is the backend saying the
 * token is expired, revoked, or reuse-detected. Unknown statuses default to
 * `unavailable` on purpose — retrying a genuinely dead token costs one wasted
 * request, while discarding a live one costs the user a trip to Discord.
 */
export function refreshFailureReason(status: number): RefreshFailure {
  return status === 401 ? "expired" : "unavailable";
}

/**
 * The signed-in desktop session: holds the access + rotating refresh tokens and
 * is the SINGLE source of truth for the access token used by both the realtime
 * transports (via a token provider) and the authenticated `verify-lcu` call.
 * {@link refresh} rotates both tokens and persists the new refresh token.
 * Constructing it performs no IO.
 */
export class DesktopSession {
  #accessToken: string;
  #refreshToken: string;
  #user: PairedUser | null;
  readonly #store: SessionStore;
  readonly #refresh: (refreshToken: string) => Promise<RefreshResult>;
  readonly #onWarn: (event: string, detail: Record<string, unknown>) => void;
  /** The in-flight rotation, shared by every concurrent caller (see refresh). */
  #inFlight: Promise<RefreshOutcome> | null = null;

  constructor(args: {
    accessToken: string;
    refreshToken: string;
    store: SessionStore;
    refreshSession: (refreshToken: string) => Promise<RefreshResult>;
    user?: PairedUser | null;
    onWarn?: (event: string, detail: Record<string, unknown>) => void;
  }) {
    this.#accessToken = args.accessToken;
    this.#refreshToken = args.refreshToken;
    this.#store = args.store;
    this.#refresh = args.refreshSession;
    this.#user = args.user ?? null;
    this.#onWarn = args.onWarn ?? ((): void => {});
  }

  get accessToken(): string {
    return this.#accessToken;
  }

  get user(): PairedUser | null {
    return this.#user;
  }

  /**
   * Silently rotate the session, COALESCED: concurrent callers share one
   * in-flight rotation.
   *
   * Coalescing is not an optimisation here. Rotation spends the presented token,
   * so a second request carrying the same one looks exactly like a stolen token
   * being replayed — and the server's answer to that is to revoke the whole
   * family. The renderer fires several authenticated IPC calls at once (teams +
   * tournaments + presence on one screen), so an expired access token produces
   * precisely that burst of simultaneous 401s.
   *
   * Never clears the store: only the CALLER knows whether a failure warrants
   * dropping to the pairing screen, and {@link RefreshOutcome} tells it which
   * kind of failure it was.
   */
  async refresh(): Promise<RefreshOutcome> {
    if (this.#inFlight !== null) {
      return this.#inFlight;
    }
    const attempt = this.#rotate().finally(() => {
      this.#inFlight = null;
    });
    this.#inFlight = attempt;
    return attempt;
  }

  /** The actual rotation, wrapped by {@link refresh}'s coalescing. */
  async #rotate(): Promise<RefreshOutcome> {
    const res = await this.#refresh(this.#refreshToken);
    if (!res.ok) {
      return { ok: false, reason: refreshFailureReason(res.status) };
    }
    this.#accessToken = res.accessToken;
    this.#refreshToken = res.refreshToken;
    try {
      // Re-persist the cached user alongside the rotated token; otherwise a
      // refresh would overwrite the stored blob and blank the paired name on the
      // next boot.
      await this.#store.save({ refreshToken: res.refreshToken, user: this.#user });
    } catch (err) {
      // The rotation SUCCEEDED; only persistence failed (no OS keychain). The
      // in-memory session is live and usable, so throwing here would discard a
      // working session over a next-boot problem — and, because this runs inside
      // an IPC handler, surface as an opaque renderer error rather than anything
      // the user could act on.
      this.#onWarn("secure_store_unavailable", {
        hint: "rotated token not persisted; session is live until the app closes",
        detail: String(err),
      });
    }
    return { ok: true };
  }
}

// --- boot decision ---------------------------------------------------------- //

/** The store surface the boot decision needs. */
export interface BootStore {
  load(): Promise<PersistedSession | null>;
  save(session: PersistedSession): Promise<void>;
  clear(): Promise<void>;
}

/** Outcome of the boot decision: straight in, or show the pairing screen. */
export type BootDecision =
  | {
      readonly kind: "signed-in";
      readonly accessToken: string;
      readonly refreshToken: string;
      /** Advisory paired-user summary restored from the encrypted store (the
       * refresh response carries none), so the UI can show the name pre-re-pair. */
      readonly user: PairedUser | null;
    }
  | { readonly kind: "needs-pairing" }
  | {
      /** We hold a token but could not reach the backend to rotate it. The token
       * is KEPT and the app retries; the user is not asked to re-pair. */
      readonly kind: "unavailable";
      readonly user: PairedUser | null;
    };

/**
 * Boot decision (replaces the old dev-login stub): if there is a stored refresh
 * token, refresh it silently and persist the ROTATED token; otherwise show the
 * pairing screen. Fully injectable — unit-tested without Electron.
 *
 * Only a 401 clears the stored token. Clearing it on ANY failed refresh is what
 * made the client demand a new pairing code every time it opened before the
 * backend was up, after a wifi drop, or during a deploy: the credential was
 * thrown away over a transient error, and only the Discord bot can mint another.
 */
export async function decideBoot(deps: {
  store: BootStore;
  refreshSession: (refreshToken: string) => Promise<RefreshResult>;
}): Promise<BootDecision> {
  const stored = await deps.store.load();
  if (stored === null) {
    return { kind: "needs-pairing" };
  }
  const res = await deps.refreshSession(stored.refreshToken);
  if (!res.ok) {
    if (refreshFailureReason(res.status) === "unavailable") {
      return { kind: "unavailable", user: stored.user ?? null };
    }
    await deps.store.clear();
    return { kind: "needs-pairing" };
  }
  // Persist the rotated refresh token on EVERY refresh (rotation invalidates the
  // predecessor server-side; a stale stored token would fail the next boot). Carry
  // the cached user forward — /refresh echoes no user, so dropping it here would
  // blank the paired name until a re-pair.
  const user = stored.user ?? null;
  await deps.store.save({ refreshToken: res.refreshToken, user });
  return {
    kind: "signed-in",
    accessToken: res.accessToken,
    refreshToken: res.refreshToken,
    user,
  };
}

// --- app runtime (per-match controllers over one WS transport) -------------- //

/** Connection + identity inputs the host app resolves from its session/login. */
export interface DesktopRuntimeConfig {
  /** Backend realtime URL, e.g. `wss://host/api/v1/realtime/ws`. */
  readonly realtimeUrl: string;
  /** Access JWT for the signed-in user (static fallback; see tokenProvider). */
  readonly token: string;
  /**
   * Live access-token source — when set it OVERRIDES {@link token}, so a silent
   * refresh is picked up on the next (re)connect without rebuilding transports.
   */
  readonly tokenProvider?: () => string;
  /** The backend's verified PUUID for this user (undefined => not verified). */
  readonly verifiedPuuid: Puuid | undefined;
  readonly heartbeatMs?: number;
}

/** A live match entry: its controller, transport, and the guild it belongs to. */
interface MatchEntry {
  readonly controller: OrchestrationController;
  readonly transport: WsBackendTransport;
}

/**
 * The per-process desktop runtime. Owns the LCU adapter, opens one WS transport
 * per match, and creates / tears down an {@link OrchestrationController} on the
 * match-assigned / match-ended lifecycle. CONSTRUCTING it opens nothing — the
 * sockets open on {@link onMatchAssigned}. Injected `wsFactory` + `clock` /
 * `scheduler` keep it unit-testable with no real IO.
 *
 * A single IDENTITY-bearing session serves matches across MANY guilds, so the
 * per-match guild is threaded through {@link onMatchAssigned} into the subscribe
 * frame rather than baked into the token. On a WS auth-rejection (4401) the
 * runtime silently refreshes once (coalesced) and reconnects every transport.
 */
export class DesktopRuntime {
  readonly #adapter: LcuAdapter;
  readonly #config: DesktopRuntimeConfig;
  readonly #wsFactory: WebSocketFactory;
  readonly #scheduler: Scheduler;
  readonly #clock: Clock;
  readonly #onWarn: (event: string, detail: Record<string, unknown>) => void;
  readonly #tokenProvider: () => string;
  readonly #refreshAuth: (() => Promise<RefreshAuthResult>) | undefined;
  readonly #onAuthLost: (() => void) | undefined;
  /** One controller + transport per live match, keyed by matchId. */
  readonly #matches = new Map<string, MatchEntry>();
  /** Coalesces concurrent 4401-driven refreshes into one in-flight attempt. */
  #authRefresh: Promise<RefreshAuthResult> | null = null;
  /** Cancels the pending "backend was unreachable, try the sockets again" timer. */
  #authRetryCancel: (() => void) | null = null;

  constructor(args: {
    adapter: LcuAdapter;
    config: DesktopRuntimeConfig;
    /** Injected for tests; defaults to the real `ws`-backed factory. */
    wsFactory?: WebSocketFactory;
    scheduler?: Scheduler;
    clock?: Clock;
    onWarn?: (event: string, detail: Record<string, unknown>) => void;
    /** Silent-refresh hook invoked on a WS 4401; returns the rotated token. */
    refreshAuth?: () => Promise<RefreshAuthResult>;
    /** Invoked when the credential is DEAD (the app must show the pairing
     * screen). Never fired for a merely-unreachable backend. */
    onAuthLost?: () => void;
  }) {
    this.#adapter = args.adapter;
    this.#config = args.config;
    this.#wsFactory = args.wsFactory ?? createWsFactory();
    this.#scheduler = args.scheduler ?? SYSTEM_SCHEDULER;
    this.#clock = args.clock ?? SYSTEM_CLOCK;
    this.#onWarn = args.onWarn ?? (() => {});
    this.#tokenProvider = args.config.tokenProvider ?? (() => args.config.token);
    this.#refreshAuth = args.refreshAuth;
    this.#onAuthLost = args.onAuthLost;
  }

  /** Run the advisory startup identity check (never blocks — PLAN.md §3.6/§2). */
  async validateIdentity(): Promise<IdentityCheckResult> {
    const result = await checkLcuIdentity({
      adapter: this.#adapter,
      verifiedPuuid: this.#config.verifiedPuuid,
    });
    if (result.verdict === "mismatch") {
      // Advisory ONLY: surface a soft warning, never block.
      this.#onWarn("identity_mismatch", { detail: result.detail });
    }
    return result;
  }

  /**
   * Create + start a controller for a newly assigned match. Idempotent. The
   * match's `guildId` (from the backend's match-assigned command) is threaded
   * into the subscribe frame for per-match WS authorization.
   *
   * ``isHost`` is the backend's own answer to "is this player opening this
   * match" and is re-applied on every call, because commands reach all ten
   * clients and only the named one may act on them (see
   * `OrchestrationController.setAssignedHost`). Re-applying is the point of
   * doing it here rather than at construction: the poll repeats, so a lease that
   * moves is picked up without tearing anything down.
   */
  onMatchAssigned(matchId: string, guildId?: string, isHost = false): void {
    const live = this.#matches.get(matchId);
    if (live !== undefined) {
      live.controller.setAssignedHost(isHost);
      return;
    }
    const transport = new WsBackendTransport(
      {
        url: this.#config.realtimeUrl,
        token: this.#tokenProvider(),
        tokenProvider: this.#tokenProvider,
        matchId,
        ...(guildId !== undefined && guildId !== "" ? { guildId } : {}),
        onWarn: this.#onWarn,
        onAuthRejected: () => {
          void this.#handleAuthRejected();
        },
      },
      this.#wsFactory,
    );
    const controller = new OrchestrationController({
      adapter: this.#adapter,
      transport,
      scheduler: this.#scheduler,
      clock: this.#clock,
      config: { matchId, heartbeatMs: this.#config.heartbeatMs ?? DEFAULT_HEARTBEAT_MS },
    });
    controller.setAssignedHost(isHost);
    this.#matches.set(matchId, { controller, transport });
    controller.start();
    transport.connect();
  }

  /** Stop + tear down the controller/transport for an ended match. */
  onMatchEnded(matchId: string): void {
    const entry = this.#matches.get(matchId);
    if (entry === undefined) {
      return;
    }
    this.#matches.delete(matchId);
    entry.controller.stop();
    entry.transport.close();
  }

  /** Tear down everything (app shutdown). */
  shutdown(): void {
    this.#authRetryCancel?.();
    this.#authRetryCancel = null;
    for (const matchId of [...this.#matches.keys()]) {
      this.onMatchEnded(matchId);
    }
  }

  /** Number of currently-live matches (for tests / diagnostics). */
  get liveMatchCount(): number {
    return this.#matches.size;
  }

  /**
   * Handle a WS auth-rejection: silently refresh ONCE (coalesced across all
   * transports that just got 4401'd) then reconnect every transport with the
   * rotated token.
   *
   * Only an EXPIRED credential surfaces auth-lost. A 4401 during a backend
   * restart used to tear the whole session down and drop the user on the pairing
   * screen mid-tournament; now it warns and lets each transport's own backed-off
   * reconnect (it schedules one after every 4401 close) try again at a paced
   * interval, by which time the backend is usually back.
   */
  async #handleAuthRejected(): Promise<void> {
    if (this.#refreshAuth === undefined) {
      this.#onAuthLost?.();
      return;
    }
    if (this.#authRefresh === null) {
      this.#authRefresh = this.#refreshAuth();
    }
    let res: RefreshAuthResult;
    try {
      res = await this.#authRefresh;
    } finally {
      this.#authRefresh = null;
    }
    if (res.ok) {
      for (const entry of this.#matches.values()) {
        entry.transport.reconnect();
      }
      return;
    }
    if (res.reason === "expired") {
      this.#onAuthLost?.();
      return;
    }
    this.#onWarn("auth_refresh_unavailable", {
      hint: "backend unreachable; session kept, retrying the sockets",
      retryInMs: AUTH_RETRY_MS,
    });
    this.#scheduleAuthRetry();
  }

  /**
   * Re-open the transports after an unreachable-backend refresh.
   *
   * The transport deliberately does NOT auto-reconnect on a 4401 (retrying with
   * a token the server just rejected is pointless), so nothing else will ever
   * bring these sockets back — leaving the session technically alive but
   * permanently mute. This paces the retry ourselves: one pending attempt at a
   * time, however many matches were rejected together. If the backend is still
   * down, the reconnect 4401s again and lands right back here, so the interval
   * is the retry loop.
   */
  #scheduleAuthRetry(): void {
    if (this.#authRetryCancel !== null) {
      return;
    }
    const cancel = this.#scheduler.every(AUTH_RETRY_MS, () => {
      this.#authRetryCancel?.();
      this.#authRetryCancel = null;
      for (const entry of this.#matches.values()) {
        entry.transport.reconnect();
      }
    });
    this.#authRetryCancel = cancel;
  }
}

// --- Electron entrypoint (only runs as the real main process) --------------- //

/** True when this module is the Electron main entrypoint, not a test import. */
function isMainEntry(): boolean {
  // In a unit test (`node --test`) `process.versions.electron` is absent, so the
  // Electron-bearing bootstrap never runs and importing this file is IO-free.
  const versions = (globalThis as { process?: { versions?: Record<string, string> } }).process
    ?.versions;
  return typeof versions?.["electron"] === "string";
}

/** Resolve the backend base URL from env or the dev default. */
function backendBaseUrl(): string {
  return process.env["BACKEND_URL"] ?? "http://localhost:8000";
}

/**
 * Boot the Electron app: open a window, run the boot decision (silent refresh or
 * pairing), wire the IPC bridge, and start the LCU status poller. Kept out of
 * module scope so importing this file never touches Electron / disk / network
 * (test import-safety).
 */
async function bootstrap(): Promise<void> {
  // Imported lazily so `electron` is never required during a unit-test import.
  const { app, BrowserWindow, ipcMain, safeStorage } = await import("electron");
  const path = await import("node:path");
  const fsp = await import("node:fs/promises");
  const fs = await import("node:fs");

  const log = (event: string, detail: Record<string, unknown> = {}): void => {
    console.log(`[desktop] ${event}`, detail);
  };

  const realtimeUrl =
    process.env["REALTIME_URL"] ?? "ws://localhost:8000/api/v1/realtime/ws";

  // Encrypted-at-rest refresh-token store under userData (never plaintext).
  const userData = app.getPath("userData");
  const store = new SecureStore({
    safeStorage,
    fs: {
      readFile: (p) => fsp.readFile(p),
      writeFile: (p, data) => fsp.writeFile(p, data),
      mkdir: (p, opts) => fsp.mkdir(p, opts),
      rm: (p, opts) => fsp.rm(p, opts),
    },
    filePath: path.join(userData, "session.bin"),
    dirPath: userData,
  });

  // Plain-JSON device preferences (non-secrets) — survives sign-out and does
  // not depend on OS keychain availability.
  const settingsStore = new SettingsStore({
    fs: {
      readFile: (p) => fsp.readFile(p),
      writeFile: (p, data) => fsp.writeFile(p, data),
      mkdir: (p, opts) => fsp.mkdir(p, opts),
    },
    filePath: path.join(userData, "settings.json"),
    dirPath: userData,
  });

  const adapter = createLcuAdapter();
  // Re-apply the device's remembered automation consent before any orchestration
  // command can arrive (the adapter itself always constructs with writes OFF).
  void settingsStore.load().then((s) => {
    if (s.automationOptIn === true) {
      adapter.setWriteAutomationOptIn(true);
      log("automation_opt_in_restored", { enabled: true });
    }
  });

  // --- mutable app state (signed-in session + per-process runtime) ---------- //
  let session: DesktopSession | null = null;
  let runtime: DesktopRuntime | null = null;
  let mainWindow: import("electron").BrowserWindow | null = null;
  /** What the renderer should show while `session` is null. "unavailable" means
   * we still HOLD a credential and are retrying — not that the user must re-pair. */
  let signedOutReason: "needs-pairing" | "unavailable" = "needs-pairing";
  /** Paired-user summary to show while unavailable (restored from the store). */
  let cachedUser: PairedUser | null = null;
  /** Match-assignment poll (see startMatchAssignmentPoll) + its latest answer. */
  let matchPollTimer: ReturnType<typeof setInterval> | null = null;
  let lastMatches: MyMatch[] = [];

  const sendToRenderer = (channel: string, payload: unknown): void => {
    mainWindow?.webContents.send(channel, payload);
  };

  /**
   * Tear the session down because the CREDENTIAL IS DEAD (a 401 from
   * `/auth/refresh`). Also wipes the stored token: it can only ever 401 again,
   * and leaving it behind means the next boot spends a request rediscovering
   * that. Never call this for a transient failure — that is the bug this whole
   * change exists to fix.
   */
  const dropSession = (): void => {
    session = null;
    stopMatchAssignmentPoll();
    runtime?.shutdown();
    runtime = null;
    signedOutReason = "needs-pairing";
    cachedUser = null;
    void store.clear();
    sendToRenderer("lol:auth-lost", {});
    log("auth_lost", { hint: "refresh returned 401; show pairing screen" });
  };

  /** Build the per-process runtime once a session exists. */
  const startRuntime = (): void => {
    if (session === null || runtime !== null) {
      return;
    }
    const active = session;
    runtime = new DesktopRuntime({
      adapter,
      config: {
        realtimeUrl,
        token: active.accessToken,
        tokenProvider: () => active.accessToken,
        verifiedPuuid: undefined,
      },
      onWarn: (e, d) => log(e, d),
      refreshAuth: async (): Promise<RefreshAuthResult> => {
        const outcome = await active.refresh();
        return outcome.ok
          ? { ok: true, token: active.accessToken }
          : { ok: false, reason: outcome.reason };
      },
      onAuthLost: () => {
        dropSession();
      },
    });

    // Escape hatch for developing against one specific match; the real wiring is
    // the poll below.
    const matchId = process.env["DESKTOP_MATCH_ID"];
    const guildId = process.env["DESKTOP_GUILD_ID"];
    if (matchId !== undefined && matchId !== "") {
      log("realtime_subscribing", { realtimeUrl, matchId, guildId });
      runtime.onMatchAssigned(matchId, guildId);
    }
    startMatchAssignmentPoll();
  };

  /**
   * Ask the backend which matches are ours, and open a realtime channel for each.
   *
   * The missing half of the orchestration loop. The backend has always published
   * create/invite/auto-accept commands onto a per-match channel, but the client
   * only ever subscribed to a match id read from an environment variable — dev
   * wiring that was never replaced. In a real tournament nobody was subscribed, so
   * every command was delivered to an empty channel and the lobby was never
   * created by anyone.
   *
   * A poll rather than a push: the client already has to survive being started
   * mid-tournament, asleep, or offline for a while, and each of those is just a
   * poll that returns the current truth. Subscribing is idempotent
   * (`onMatchAssigned` no-ops for a match it already services), so a repeated
   * answer costs nothing.
   */
  const startMatchAssignmentPoll = (): void => {
    if (matchPollTimer !== null) return;
    const poll = async (): Promise<void> => {
      if (session === null || runtime === null) return;
      const result = await withSession((t) =>
        listMyMatches({ accessToken: t }, { backendUrl: backendBaseUrl() }),
      );
      if (!result.ok) {
        // A blip must not kill the loop — the next tick simply asks again.
        log("match_poll_failed", { status: result.status });
        return;
      }
      for (const match of result.matches) {
        runtime?.onMatchAssigned(match.matchId, match.guildId, match.isHost);
      }
      // The backend stops listing a match once it completes. Tear those
      // controllers down — without this every finished match kept its
      // heartbeat, WS socket and (for guests) 2s invite poll alive until the
      // app itself was closed.
      for (const previous of lastMatches) {
        if (!result.matches.some((m) => m.matchId === previous.matchId)) {
          runtime?.onMatchEnded(previous.matchId);
        }
      }
      lastMatches = result.matches;
    };
    void poll();
    matchPollTimer = setInterval(() => void poll(), MATCH_POLL_MS);
  };

  const stopMatchAssignmentPoll = (): void => {
    if (matchPollTimer !== null) {
      clearInterval(matchPollTimer);
      matchPollTimer = null;
    }
    lastMatches = [];
  };

  /**
   * Run an authenticated backend call with the current session: on a 401,
   * silently refresh once and retry.
   *
   * The two ways a refresh can fail are deliberately NOT collapsed. Only an
   * EXPIRED credential ends the session; a backend we could not reach leaves the
   * session exactly as it was and reports a transient failure, so a blip
   * mid-tournament costs the user a retry instead of a new pairing code.
   */
  const withSession = async <T extends { ok: boolean; status?: number }>(
    call: (accessToken: string) => Promise<T>,
  ): Promise<T | { ok: false; status: number; detail: string }> => {
    const active = session;
    if (active === null) {
      return { ok: false, status: 401, detail: "Not signed in. Enter your code first." };
    }
    const result = await call(active.accessToken);
    if (result.ok || result.status !== 401) {
      return result;
    }
    const outcome = await active.refresh();
    if (outcome.ok) {
      return call(active.accessToken);
    }
    if (outcome.reason === "expired") {
      dropSession();
      return { ok: false, status: 401, detail: "Session expired. Enter a new code to sign in." };
    }
    // status 0 = "never reached the backend", the same code the transport layer
    // uses, so the renderer renders it as a connectivity problem and not as a
    // rejected request.
    return { ok: false, status: 0, detail: "Can't reach the server. Try again in a moment." };
  };

  // --- IPC bridge (preload exposes window.lol.*) ---------------------------- //

  // READ-ONLY LCU current-summoner read (used by the verify step + poller).
  ipcMain.handle("lol:read-summoner", async () => {
    const result = await readCurrentSummoner();
    if (result.ok) {
      return {
        ok: true,
        puuid: result.puuid,
        summonerName: result.displayName,
        gameName: result.gameName,
        tagLine: result.tagLine,
        summonerId: result.summonerId,
        region: result.region,
      };
    }
    return { ok: false, reason: result.reason, detail: result.detail };
  });

  // Current session state so the renderer chooses pairing vs verify on load.
  // `status` distinguishes the two ways of being signed out: "needs-pairing"
  // (no credential — ask for a code) from "unavailable" (we HAVE one, the
  // backend is just unreachable — show a retry, never a code prompt).
  ipcMain.handle("lol:session-state", () => ({
    signedIn: session !== null,
    user: session?.user ?? cachedUser,
    status: session !== null ? "signed-in" : signedOutReason,
  }));

  // Exchange a pairing code for a session, persist the refresh token, and start.
  ipcMain.handle(
    "lol:pair",
    async (_event, args: { code: string; deviceLabel?: string }) => {
      const result: PairResult = await pairDesktop(
        { code: args.code, ...(args.deviceLabel ? { deviceLabel: args.deviceLabel } : {}) },
        { backendUrl: backendBaseUrl() },
      );
      if (!result.ok) {
        return { ok: false, status: result.status, detail: result.detail };
      }
      if (store.isEncryptionAvailable()) {
        await store.save({ refreshToken: result.refreshToken, user: result.user });
      } else {
        log("secure_store_unavailable", { hint: "refresh token not persisted (no OS keychain)" });
      }
      session = new DesktopSession({
        accessToken: result.accessToken,
        refreshToken: result.refreshToken,
        store,
        refreshSession: (rt) => refreshSession(rt, { backendUrl: backendBaseUrl() }),
        user: result.user,
        onWarn: (e, d) => log(e, d),
      });
      signedOutReason = "needs-pairing";
      cachedUser = result.user;
      startRuntime();
      return { ok: true, user: result.user, account: result.account };
    },
  );

  // Explicit silent refresh (renderer-driven; boot/4401 paths refresh too).
  ipcMain.handle("lol:refresh", async () => {
    if (session === null) {
      return { ok: false };
    }
    const outcome = await session.refresh();
    if (!outcome.ok && outcome.reason === "expired") {
      dropSession();
    }
    return { ok: outcome.ok };
  });

  // Authenticated step-2 account link (no code). On 401 refresh once + retry.
  ipcMain.handle("lol:verify", async () => {
    const result: VerifyResult | { ok: false; status: number; detail: string } = await withSession(
      (t) => verifyDesktop({ accessToken: t }),
    );
    if (result.ok) {
      return {
        ok: true,
        accountId: result.accountId,
        puuid: result.puuid,
        ...(result.tier !== undefined ? { tier: result.tier } : {}),
        ...(result.division !== undefined ? { division: result.division } : {}),
        ...(result.leaguePoints !== undefined ? { leaguePoints: result.leaguePoints } : {}),
        ...(result.rankText !== undefined ? { rankText: result.rankText } : {}),
        ...(result.gameName !== undefined ? { gameName: result.gameName } : {}),
        ...(result.tagLine !== undefined ? { tagLine: result.tagLine } : {}),
      };
    }
    return { ok: false, status: result.status, detail: result.detail };
  });

  // The signed-in user's own linked accounts (renderer shows them + "link another").
  ipcMain.handle("lol:list-accounts", () =>
    withSession((t) => listMyAccounts({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );

  // The caller's live matches + lobby state — what the Live match screen renders.
  // Served from the poll's latest answer when it has one, so opening the screen
  // shows the same thing the realtime subscription is already acting on.
  ipcMain.handle("lol:matches-mine", async () => {
    const result = await withSession((t) =>
      listMyMatches({ accessToken: t }, { backendUrl: backendBaseUrl() }),
    );
    if (result.ok) lastMatches = result.matches;
    return result.ok ? { ok: true, matches: lastMatches } : result;
  });

  // The signed-in user's tournaments + join-by-code.
  ipcMain.handle("lol:tournaments-mine", () =>
    withSession((t) => listMyTournaments({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );
  ipcMain.handle("lol:tournament-join", (_event, args: { code: string; teamId?: string }) =>
    withSession((t) =>
      joinTournamentByCode(
        { accessToken: t, code: args.code, ...(args.teamId ? { teamId: args.teamId } : {}) },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  // Per-user dismissal — clears a finished tournament off THIS user's list only.
  ipcMain.handle("lol:tournament-hide", (_event, args: { tournamentId: string }) =>
    withSession((t) =>
      hideTournament(
        { accessToken: t, tournamentId: args.tournamentId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle("lol:tournament-bracket", (_event, args: { tournamentId: string }) =>
    withSession((t) =>
      getBracket(
        { accessToken: t, tournamentId: args.tournamentId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle("lol:tournament-lookup", (_event, args: { code: string }) =>
    withSession((t) =>
      lookupTournamentByCode({ accessToken: t, code: args.code }, { backendUrl: backendBaseUrl() }),
    ),
  );
  ipcMain.handle("lol:tournament-entrants", (_event, args: { tournamentId: string }) =>
    withSession((t) =>
      listEntrants(
        { accessToken: t, tournamentId: args.tournamentId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle(
    "lol:tournament-lock-in",
    (_event, args: { tournamentId: string; teamId?: string }) =>
      withSession((t) =>
        lockIn(
          {
            accessToken: t,
            tournamentId: args.tournamentId,
            ...(args.teamId ? { teamId: args.teamId } : {}),
            // Lock-in doubles as hosting consent: the automation toggle IS the
            // player's "may my client run the lobby?" answer, so the current
            // value rides along and the orchestrator can seed them as a host.
            hostOptIn: adapter.writeAutomationEnabled(),
          },
          { backendUrl: backendBaseUrl() },
        ),
      ),
  );
  ipcMain.handle("lol:match-ready", (_event, args: { matchId: string }) =>
    withSession((t) =>
      markReady({ accessToken: t, matchId: args.matchId }, { backendUrl: backendBaseUrl() }),
    ),
  );

  // Teams: create (captain + join code), join-by-code, roster, captain join code.
  ipcMain.handle("lol:team-create", (_event, args: { name: string }) =>
    withSession((t) => createTeam({ accessToken: t, name: args.name }, { backendUrl: backendBaseUrl() })),
  );
  ipcMain.handle("lol:team-join", (_event, args: { code: string }) =>
    withSession((t) => joinTeamByCode({ accessToken: t, code: args.code }, { backendUrl: backendBaseUrl() })),
  );
  ipcMain.handle("lol:team-members", (_event, args: { teamId: string }) =>
    withSession((t) =>
      listTeamMembers({ accessToken: t, teamId: args.teamId }, { backendUrl: backendBaseUrl() }),
    ),
  );
  ipcMain.handle("lol:team-code", (_event, args: { teamId: string }) =>
    withSession((t) =>
      getTeamJoinCode({ accessToken: t, teamId: args.teamId }, { backendUrl: backendBaseUrl() }),
    ),
  );

  // Team-leader approval flow: the captain's pending-request feed + approve/reject.
  ipcMain.handle("lol:team-join-requests", () =>
    withSession((t) => listMyJoinRequests({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );
  ipcMain.handle("lol:team-approve", (_event, args: { teamId: string; userId: string }) =>
    withSession((t) =>
      approveJoinRequest(
        { accessToken: t, teamId: args.teamId, userId: args.userId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle("lol:team-reject", (_event, args: { teamId: string; userId: string }) =>
    withSession((t) =>
      rejectJoinRequest(
        { accessToken: t, teamId: args.teamId, userId: args.userId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle("lol:team-remove-member", (_event, args: { teamId: string; userId: string }) =>
    withSession((t) =>
      removeMember(
        { accessToken: t, teamId: args.teamId, userId: args.userId },
        { backendUrl: backendBaseUrl() },
      ),
    ),
  );
  ipcMain.handle("lol:teams-mine", () =>
    withSession((t) => listMyTeams({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );
  ipcMain.handle("lol:team-leave", (_event, args: { teamId: string }) =>
    withSession((t) =>
      leaveTeam({ accessToken: t, teamId: args.teamId }, { backendUrl: backendBaseUrl() }),
    ),
  );
  ipcMain.handle("lol:team-delete", (_event, args: { teamId: string }) =>
    withSession((t) =>
      deleteTeam({ accessToken: t, teamId: args.teamId }, { backendUrl: backendBaseUrl() }),
    ),
  );

  // Unregister one linked Riot account.
  ipcMain.handle("lol:account-unlink", (_event, args: { accountId: string }) =>
    withSession((t) =>
      unlinkAccount({ accessToken: t, accountId: args.accountId }, { backendUrl: backendBaseUrl() }),
    ),
  );

  // Explicit sign-out: drop the in-memory session, tear down the runtime, and
  // wipe the persisted refresh token so the next boot shows the pairing screen.
  // The renderer calls this after the user unregisters their LAST linked account
  // (0 remaining) — an account-less session has nothing to orchestrate, so we
  // return to step 1. Mirrors the onAuthLost teardown, minus the auth-lost event
  // (the renderer drives the view transition itself). Idempotent.
  ipcMain.handle("lol:sign-out", async () => {
    session = null;
    runtime?.shutdown();
    runtime = null;
    signedOutReason = "needs-pairing";
    cachedUser = null;
    await store.clear();
    log("signed_out", { hint: "user cleared session; show pairing screen" });
    return { ok: true };
  });

  // Global live presence (bell badge + panel).
  ipcMain.handle("lol:presence", () =>
    withSession((t) => getPresence({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );

  // --- LCU write automation: the player's remembered consent ----------------- //
  // Every lobby write (create, invite, accept) is refused until this is granted,
  // which is the design (PLAN.md §3.3). The answer is PERSISTED per device
  // (settings.json, non-secret): re-demanding consent every launch trained
  // players to ignore the toggle and left every match without an eligible host.
  // It remains an explicit opt-in — a fresh install starts OFF — and turning it
  // off is persisted just as durably.
  ipcMain.handle("lol:automation-get", () => ({
    ok: true,
    enabled: adapter.writeAutomationEnabled(),
  }));
  ipcMain.handle("lol:automation-set", (_e, args: { enabled?: unknown }) => {
    const enabled = args?.enabled === true;
    adapter.setWriteAutomationOptIn(enabled);
    void settingsStore.save({ automationOptIn: enabled });
    log("automation_opt_in", { enabled });
    return { ok: true, enabled };
  });

  // --- LCU reachability poller (3-5s) -> renderer badge --------------------- //
  const poller = new LcuStatusPoller({
    probeSummoner: async () => (await readCurrentSummoner()).ok,
    probeLockfile: async () => {
      // Reuse the adapter's platform lockfile-path resolution: a successful read
      // means the client process is up (even if no summoner is signed in yet).
      try {
        await adapter.readLockfileAuth();
        return true;
      } catch {
        return false;
      }
    },
    onStatus: (status: LcuStatus) => {
      sendToRenderer("lol:lcu-status", status);
      log("lcu_status", { status });
    },
  });

  const createWindow = (): import("electron").BrowserWindow => {
    const win = new BrowserWindow({
      width: 1280,
      height: 820,
      minWidth: 1120,
      minHeight: 720,
      title: "LeagueOfLeagues — Desktop",
      backgroundColor: "#010a13",
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        // CommonJS preload (.cjs): the package is "type":"module", so a .js preload
        // would be ESM, which Electron only loads from a .mjs file — loading it as a
        // .js fails silently ("Unable to load preload script") and window.lol is
        // undefined. A .cjs preload loads reliably under contextIsolation.
        preload: path.join(app.getAppPath(), "dist", "preload.cjs"),
      },
    });
    // Prefer the React (Vite) renderer built to dist-renderer/ by `npm run build`;
    // fall back to the legacy vanilla renderer if that build is absent.
    const reactRenderer = path.join(app.getAppPath(), "dist-renderer", "index.html");
    const legacyRenderer = path.join(app.getAppPath(), "static", "index.html");
    const rendererPath = fs.existsSync(reactRenderer) ? reactRenderer : legacyRenderer;
    log("renderer.load", { path: rendererPath, react: rendererPath === reactRenderer });
    void win.loadFile(rendererPath);
    return win;
  };

  await app.whenReady();
  mainWindow = createWindow();

  // Boot decision: silent refresh if we have a stored token, else pairing. When
  // the backend is unreachable the token is KEPT and this retries in the
  // background — the user is never sent to Discord for a new code over a server
  // that is merely down or not up yet.
  const BOOT_RETRY_FIRST_MS = 5_000;
  const BOOT_RETRY_MAX_MS = 60_000;

  /** Run the boot decision; on "unavailable", schedule a backed-off retry. */
  const attemptBoot = async (retryDelayMs: number): Promise<void> => {
    if (session !== null) {
      return; // The user paired by hand while we were waiting; nothing to restore.
    }
    const decision = await decideBoot({
      store,
      refreshSession: (rt) => refreshSession(rt, { backendUrl: backendBaseUrl() }),
    });
    if (decision.kind === "signed-in") {
      session = new DesktopSession({
        accessToken: decision.accessToken,
        refreshToken: decision.refreshToken,
        store,
        refreshSession: (rt) => refreshSession(rt, { backendUrl: backendBaseUrl() }),
        user: decision.user,
        onWarn: (e, d) => log(e, d),
      });
      signedOutReason = "needs-pairing";
      cachedUser = decision.user;
      startRuntime();
      // Tells a renderer that is showing "reconnecting…" to load the session. On
      // the first attempt nothing is listening yet, which is fine: the renderer
      // reads the state itself when it mounts.
      sendToRenderer("lol:session-restored", { user: decision.user });
      log("booted_signed_in", {});
      return;
    }
    if (decision.kind === "unavailable") {
      signedOutReason = "unavailable";
      cachedUser = decision.user;
      log("boot_backend_unavailable", { retryInMs: retryDelayMs });
      setTimeout(() => {
        void attemptBoot(Math.min(retryDelayMs * 2, BOOT_RETRY_MAX_MS));
      }, retryDelayMs);
      return;
    }
    signedOutReason = "needs-pairing";
    log("booted_needs_pairing", {});
  };

  await attemptBoot(BOOT_RETRY_FIRST_MS);

  // Status poller runs regardless of sign-in (the badge is always visible).
  poller.start();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow();
  });

  app.on("window-all-closed", () => {
    poller.stop();
    if (process.platform !== "darwin") app.quit();
  });
}

if (isMainEntry()) {
  void bootstrap();
}
