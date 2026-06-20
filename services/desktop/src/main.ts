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
 * ToS posture (PLAN.md §3.3 / §9): every LCU **write** is gated behind a
 * per-session, default-OFF opt-in + presence gate + human-paced rate limits +
 * a recorded consent + a global kill-switch. Korea is excluded. No in-match
 * gameplay automation, ever.
 *
 * IMPORT-SAFETY (load-bearing for tests): importing this module performs NO IO.
 * It must not open a WebSocket, read the lockfile, or create an Electron window.
 * All of that lives behind `bootstrap()` / `runApp()`, invoked only from the
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
  type BackendTransport,
  type Clock,
  type Scheduler,
} from "./orchestration/index.js";
import {
  WsBackendTransport,
  createWsFactory,
  type WebSocketFactory,
} from "./transport/wsTransport.js";
import { checkLcuIdentity, type IdentityCheckResult } from "./identity/identityCheck.js";

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

// --- app runtime (per-match controllers over one WS transport) -------------- //

/** Connection + identity inputs the host app resolves from its session/login. */
export interface DesktopRuntimeConfig {
  /** Backend realtime URL, e.g. `wss://host/api/v1/realtime/ws`. */
  readonly realtimeUrl: string;
  /** Access JWT for the signed-in user. */
  readonly token: string;
  /** The backend's verified PUUID for this user (undefined => not verified). */
  readonly verifiedPuuid: Puuid | undefined;
  readonly heartbeatMs?: number;
}

/**
 * The per-process desktop runtime. Owns the LCU adapter, opens one WS transport
 * per match, and creates / tears down an {@link OrchestrationController} on the
 * match-assigned / match-ended lifecycle. CONSTRUCTING it opens nothing — the
 * sockets open on {@link onMatchAssigned}. Injected `wsFactory` + `clock` /
 * `scheduler` keep it unit-testable with no real IO.
 */
export class DesktopRuntime {
  readonly #adapter: LcuAdapter;
  readonly #config: DesktopRuntimeConfig;
  readonly #wsFactory: WebSocketFactory;
  readonly #scheduler: Scheduler;
  readonly #clock: Clock;
  readonly #onWarn: (event: string, detail: Record<string, unknown>) => void;
  /** One controller + transport per live match, keyed by matchId. */
  readonly #matches = new Map<string, { controller: OrchestrationController; transport: BackendTransport & { close(): void } }>();

  constructor(args: {
    adapter: LcuAdapter;
    config: DesktopRuntimeConfig;
    /** Injected for tests; defaults to the real `ws`-backed factory. */
    wsFactory?: WebSocketFactory;
    scheduler?: Scheduler;
    clock?: Clock;
    onWarn?: (event: string, detail: Record<string, unknown>) => void;
  }) {
    this.#adapter = args.adapter;
    this.#config = args.config;
    this.#wsFactory = args.wsFactory ?? createWsFactory();
    this.#scheduler = args.scheduler ?? SYSTEM_SCHEDULER;
    this.#clock = args.clock ?? SYSTEM_CLOCK;
    this.#onWarn = args.onWarn ?? (() => {});
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

  /** Create + start a controller for a newly assigned match. Idempotent. */
  onMatchAssigned(matchId: string): void {
    if (this.#matches.has(matchId)) {
      return;
    }
    const transport = new WsBackendTransport(
      {
        url: this.#config.realtimeUrl,
        token: this.#config.token,
        matchId,
        onWarn: this.#onWarn,
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

/**
 * Boot the Electron app: open a window and (in a later phase) wire the
 * {@link DesktopRuntime} to the authenticated session. Kept out of module scope
 * so importing this file never touches Electron (test import-safety).
 */
async function bootstrap(): Promise<void> {
  // Imported lazily so `electron` is never required during a unit-test import.
  const { app, BrowserWindow } = await import("electron");

  const createWindow = (): unknown => {
    const win = new BrowserWindow({
      width: 1024,
      height: 720,
      webPreferences: { contextIsolation: true, nodeIntegration: false },
    });
    // Phase 0: a placeholder local page is loaded in a later phase.
    return win;
  };

  await app.whenReady();

  // Constructed here so the adapter's lifecycle follows the app's. The runtime
  // (WS transport + per-match controllers) is wired to the authenticated
  // session in a later phase; identity validation runs on startup.
  const adapter = createLcuAdapter();
  void adapter;

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}

if (isMainEntry()) {
  void bootstrap();
}
