/**
 * LCU reachability poller (PLAN.md §3.6 / docs/desktop-auth-plan.md Phase C).
 *
 * Polls the local League client every few seconds and classifies it into a
 * three-state badge the renderer shows:
 *   - `online`  : lockfile present AND `current-summoner` responds — fully
 *                 signed in; "Verify with League" is enabled only in this state.
 *   - `loading` : lockfile present but the summoner read fails — the client is
 *                 starting / the user is on the login screen.
 *   - `offline` : no lockfile — League is not running.
 *
 * It reuses the same READ-ONLY signals as the heartbeat path: the lockfile read
 * (own-client auth) and `getCurrentSummoner`. Both probes are INJECTED so the
 * poller is unit-testable with no real LCU. CONSTRUCTING it polls nothing —
 * {@link start} kicks off the timer; {@link tick} runs a single probe (used by
 * tests and the immediate first poll).
 */

export type LcuStatus = "online" | "loading" | "offline";

/** Injected timer seam (real impl wraps setInterval; tests drive it manually). */
export interface PollerTimers {
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

const DEFAULT_TIMERS: PollerTimers = {
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (h) => clearInterval(h as ReturnType<typeof setInterval>),
};

export interface StatusPollerConfig {
  /**
   * True if the local `current-summoner` read succeeds (fully signed in). When
   * this is true the lockfile is necessarily present, so it implies `online`.
   */
  readonly probeSummoner: () => Promise<boolean>;
  /** True if the LCU lockfile exists/readable (client process is up). */
  readonly probeLockfile: () => Promise<boolean>;
  /** Called whenever the computed status CHANGES (and once on the first tick). */
  readonly onStatus: (status: LcuStatus) => void;
  /** Poll cadence; clamped to the 3–5s band (default 4s). */
  readonly intervalMs?: number;
  readonly timers?: PollerTimers;
}

/** Clamp the requested cadence into the documented 3–5s band. */
function clampInterval(ms: number | undefined): number {
  const v = ms ?? 4_000;
  return Math.min(5_000, Math.max(3_000, v));
}

/**
 * Periodic LCU reachability poller. Emits {@link LcuStatus} transitions via the
 * injected `onStatus` callback (deduped — only on change, plus the first tick).
 */
export class LcuStatusPoller {
  readonly #config: StatusPollerConfig;
  readonly #timers: PollerTimers;
  readonly #intervalMs: number;
  #handle: unknown = null;
  #last: LcuStatus | null = null;
  /** Guards against overlapping ticks if a probe is slower than the interval. */
  #inFlight = false;

  constructor(config: StatusPollerConfig) {
    this.#config = config;
    this.#timers = config.timers ?? DEFAULT_TIMERS;
    this.#intervalMs = clampInterval(config.intervalMs);
  }

  /** Begin polling: an immediate tick, then every {@link intervalMs}. Idempotent. */
  start(): void {
    if (this.#handle !== null) {
      return;
    }
    void this.tick();
    this.#handle = this.#timers.setInterval(() => {
      void this.tick();
    }, this.#intervalMs);
  }

  /** Stop polling. The last emitted status is retained (so a restart re-dedupes). */
  stop(): void {
    if (this.#handle !== null) {
      this.#timers.clearInterval(this.#handle);
      this.#handle = null;
    }
  }

  /** The most recently computed status, or `null` before the first tick. */
  get status(): LcuStatus | null {
    return this.#last;
  }

  /**
   * Run a single probe, emit on change, and return the computed status. Probe
   * errors are treated as `offline` (fail-safe — never throws).
   */
  async tick(): Promise<LcuStatus> {
    if (this.#inFlight) {
      return this.#last ?? "offline";
    }
    this.#inFlight = true;
    let status: LcuStatus;
    try {
      status = await this.#compute();
    } catch {
      status = "offline";
    } finally {
      this.#inFlight = false;
    }
    if (status !== this.#last) {
      this.#last = status;
      this.#config.onStatus(status);
    }
    return status;
  }

  async #compute(): Promise<LcuStatus> {
    // A successful summoner read implies the lockfile is present => online.
    if (await this.#config.probeSummoner()) {
      return "online";
    }
    // No summoner: lockfile present means the client is up but not signed in.
    return (await this.#config.probeLockfile()) ? "loading" : "offline";
  }
}
