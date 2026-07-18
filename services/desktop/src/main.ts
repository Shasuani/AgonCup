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
  LcuAdapterSkeleton,
  type LcuAdapter,
  type LcuAdapterConfig,
  type LcuSchemaVersion,
  type Puuid,
} from "@lol/lcu";
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
import { getBracket, joinTournamentByCode, listMyTournaments } from "./tournaments/tournaments.js";
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
import { LcuStatusPoller, type LcuStatus } from "./lcu/statusPoller.js";

/** Default heartbeat cadence while pre-game/in-game (PLAN.md §3.6.1 ~2-3s). */
const DEFAULT_HEARTBEAT_MS = 2_500;

/** Build the LCU adapter with writes disabled by default (opt-in per session). */
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
  return new LcuAdapterSkeleton(config);
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

  constructor(args: {
    accessToken: string;
    refreshToken: string;
    store: SessionStore;
    refreshSession: (refreshToken: string) => Promise<RefreshResult>;
    user?: PairedUser | null;
  }) {
    this.#accessToken = args.accessToken;
    this.#refreshToken = args.refreshToken;
    this.#store = args.store;
    this.#refresh = args.refreshSession;
    this.#user = args.user ?? null;
  }

  get accessToken(): string {
    return this.#accessToken;
  }

  get user(): PairedUser | null {
    return this.#user;
  }

  /**
   * Silently rotate the session. On success updates the in-memory tokens and
   * PERSISTS the rotated refresh token (every refresh persists). Returns false
   * (without clearing) when the refresh token is expired/revoked/reused — the
   * caller drops to the pairing screen.
   */
  async refresh(): Promise<boolean> {
    const res = await this.#refresh(this.#refreshToken);
    if (!res.ok) {
      return false;
    }
    this.#accessToken = res.accessToken;
    this.#refreshToken = res.refreshToken;
    // Re-persist the cached user alongside the rotated token; otherwise a refresh
    // would overwrite the stored blob and blank the paired name on the next boot.
    await this.#store.save({ refreshToken: res.refreshToken, user: this.#user });
    return true;
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
  | { readonly kind: "needs-pairing" };

/**
 * Boot decision (replaces the old dev-login stub): if there is a stored refresh
 * token, refresh it silently and persist the ROTATED token; otherwise show the
 * pairing screen. A failed refresh (expired/revoked) clears the stored token and
 * also drops to pairing. Fully injectable — unit-tested without Electron.
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
  readonly #refreshAuth:
    | (() => Promise<{ ok: true; token: string } | { ok: false }>)
    | undefined;
  readonly #onAuthLost: (() => void) | undefined;
  /** One controller + transport per live match, keyed by matchId. */
  readonly #matches = new Map<string, MatchEntry>();
  /** Coalesces concurrent 4401-driven refreshes into one in-flight attempt. */
  #authRefresh: Promise<boolean> | null = null;

  constructor(args: {
    adapter: LcuAdapter;
    config: DesktopRuntimeConfig;
    /** Injected for tests; defaults to the real `ws`-backed factory. */
    wsFactory?: WebSocketFactory;
    scheduler?: Scheduler;
    clock?: Clock;
    onWarn?: (event: string, detail: Record<string, unknown>) => void;
    /** Silent-refresh hook invoked on a WS 4401; returns the rotated token. */
    refreshAuth?: () => Promise<{ ok: true; token: string } | { ok: false }>;
    /** Invoked when a refresh fails (the app must show the pairing screen). */
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
   */
  onMatchAssigned(matchId: string, guildId?: string): void {
    if (this.#matches.has(matchId)) {
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
   * rotated token. If refresh fails, surface auth-lost so the app re-pairs.
   */
  async #handleAuthRejected(): Promise<void> {
    if (this.#refreshAuth === undefined) {
      this.#onAuthLost?.();
      return;
    }
    if (this.#authRefresh === null) {
      const refreshAuth = this.#refreshAuth;
      this.#authRefresh = (async () => {
        const res = await refreshAuth();
        return res.ok;
      })();
    }
    let ok = false;
    try {
      ok = await this.#authRefresh;
    } finally {
      this.#authRefresh = null;
    }
    if (ok) {
      for (const entry of this.#matches.values()) {
        entry.transport.reconnect();
      }
    } else {
      this.#onAuthLost?.();
    }
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

  const adapter = createLcuAdapter();

  // --- mutable app state (signed-in session + per-process runtime) ---------- //
  let session: DesktopSession | null = null;
  let runtime: DesktopRuntime | null = null;
  let mainWindow: import("electron").BrowserWindow | null = null;

  const sendToRenderer = (channel: string, payload: unknown): void => {
    mainWindow?.webContents.send(channel, payload);
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
      refreshAuth: async () =>
        (await active.refresh())
          ? { ok: true, token: active.accessToken }
          : { ok: false },
      onAuthLost: () => {
        session = null;
        runtime?.shutdown();
        runtime = null;
        sendToRenderer("lol:auth-lost", {});
        log("auth_lost", { hint: "refresh failed; show pairing screen" });
      },
    });

    // Dev wiring: join a single match's realtime channel if configured. Real
    // match-assigned wiring threads each match's guildId from the backend.
    const matchId = process.env["DESKTOP_MATCH_ID"];
    const guildId = process.env["DESKTOP_GUILD_ID"];
    if (matchId !== undefined && matchId !== "") {
      log("realtime_subscribing", { realtimeUrl, matchId, guildId });
      runtime.onMatchAssigned(matchId, guildId);
    } else {
      log("realtime_idle", { hint: "set DESKTOP_MATCH_ID (+DESKTOP_GUILD_ID) to join a match" });
    }
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
  ipcMain.handle("lol:session-state", () => ({
    signedIn: session !== null,
    user: session?.user ?? null,
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
      });
      startRuntime();
      return { ok: true, user: result.user, account: result.account };
    },
  );

  // Explicit silent refresh (renderer-driven; boot/4401 paths refresh too).
  ipcMain.handle("lol:refresh", async () => {
    if (session === null) {
      return { ok: false };
    }
    return { ok: await session.refresh() };
  });

  // Authenticated step-2 account link (no code). On 401 refresh once + retry.
  ipcMain.handle("lol:verify", async () => {
    if (session === null) {
      return { ok: false, status: 401, detail: "Not signed in. Enter your code first." };
    }
    let result: VerifyResult = await verifyDesktop({ accessToken: session.accessToken });
    if (!result.ok && result.status === 401) {
      // Access token expired mid-flight: silent refresh + one retry.
      if (await session.refresh()) {
        result = await verifyDesktop({ accessToken: session.accessToken });
      } else {
        session = null;
        sendToRenderer("lol:auth-lost", {});
        return { ok: false, status: 401, detail: "Session expired. Enter a new code to sign in." };
      }
    }
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
  // Refreshes the session once on a 401, mirroring lol:verify.
  ipcMain.handle("lol:list-accounts", async () => {
    if (session === null) {
      return { ok: false, status: 401, detail: "Not signed in." };
    }
    let result: ListAccountsResult = await listMyAccounts(
      { accessToken: session.accessToken },
      { backendUrl: backendBaseUrl() },
    );
    if (!result.ok && result.status === 401) {
      if (await session.refresh()) {
        result = await listMyAccounts(
          { accessToken: session.accessToken },
          { backendUrl: backendBaseUrl() },
        );
      } else {
        session = null;
        sendToRenderer("lol:auth-lost", {});
        return { ok: false, status: 401, detail: "Session expired." };
      }
    }
    return result;
  });

  // Run an authenticated backend call with the current session: on a 401,
  // silently refresh once and retry (mirrors lol:verify / lol:list-accounts). An
  // unrecoverable 401 clears the session and notifies the renderer to re-pair.
  const withSession = async <T extends { ok: boolean; status?: number }>(
    call: (accessToken: string) => Promise<T>,
  ): Promise<T | { ok: false; status: number; detail: string }> => {
    const active = session;
    if (active === null) {
      return { ok: false, status: 401, detail: "Not signed in." };
    }
    let result = await call(active.accessToken);
    if (!result.ok && result.status === 401) {
      if (await active.refresh()) {
        result = await call(active.accessToken);
      } else {
        session = null;
        sendToRenderer("lol:auth-lost", {});
        return { ok: false, status: 401, detail: "Session expired." };
      }
    }
    return result;
  };

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
  ipcMain.handle("lol:tournament-bracket", (_event, args: { tournamentId: string }) =>
    withSession((t) =>
      getBracket(
        { accessToken: t, tournamentId: args.tournamentId },
        { backendUrl: backendBaseUrl() },
      ),
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
    await store.clear();
    log("signed_out", { hint: "user cleared session; show pairing screen" });
    return { ok: true };
  });

  // Global live presence (bell badge + panel).
  ipcMain.handle("lol:presence", () =>
    withSession((t) => getPresence({ accessToken: t }, { backendUrl: backendBaseUrl() })),
  );

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
      width: 1024,
      height: 720,
      title: "LeagueOfLeagues — Desktop",
      backgroundColor: "#0a0b0f",
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
    void win.loadFile(path.join(app.getAppPath(), "static", "index.html"));
    return win;
  };

  await app.whenReady();
  mainWindow = createWindow();

  // Boot decision: silent refresh if we have a stored token, else pairing.
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
    });
    startRuntime();
    log("booted_signed_in", {});
  } else {
    log("booted_needs_pairing", {});
  }

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
