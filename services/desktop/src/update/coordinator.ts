/**
 * The update state machine the UI and the timers drive (PLAN.md §3.3).
 *
 *   idle ─check─▶ up_to_date | unknown | available ─prepare─▶ downloading ─▶ ready ─install─▶ installing
 *                                                       └──▶ failed (retry = install again)
 *
 * - A **silent** check runs shortly after boot and then every 30 minutes; a
 *   **manual** one comes from the "Check for updates" link. Checks never throw
 *   and never disturb a download/install in flight.
 * - The first time a version is seen the native notification fires (once per
 *   version per process) and, when a platform installer exists, the package is
 *   fetched in the background so the banner can offer an instant "Restart".
 * - Every state change is pushed to the renderer through `onState`.
 */

import type { Scheduler } from "../orchestration/index.js";
import type { ReleaseInfo, VersionCheckResult } from "./check.js";
import type { Installer } from "./installers.js";

/** Silent re-check cadence — tournament clients stay open for hours. */
export const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1_000;
/** Grace before the first check, so it never races the boot refresh + window. */
export const UPDATE_FIRST_CHECK_DELAY_MS = 10_000;

export type UpdatePhase =
  | "idle"
  | "checking"
  | "up_to_date"
  | "unknown"
  | "available"
  | "downloading"
  | "ready"
  | "installing"
  | "failed";

export type CheckReason = "startup" | "periodic" | "manual";

export interface UpdateState {
  readonly current: string;
  readonly platform: string;
  /** Can this build install the update itself (packaged + a platform strategy)? */
  readonly supported: boolean;
  readonly phase: UpdatePhase;
  readonly latest: string | null;
  readonly required: boolean;
  /** The manual installer link (always usable, even when unsupported/failed). */
  readonly downloadUrl: string | null;
  /** 0..100 while downloading. */
  readonly progress: number | null;
  readonly error: string | null;
  readonly lastCheckedAt: string | null;
}

export interface UpdatesDeps {
  readonly currentVersion: string;
  readonly platform: string;
  readonly check: (args: {
    readonly currentVersion: string;
    readonly platform: string;
  }) => Promise<VersionCheckResult>;
  readonly installer: Installer | null;
  readonly scheduler: Scheduler;
  readonly now: () => number;
  readonly onState: (state: UpdateState) => void;
  /** Native "update available" notification; called once per version. */
  readonly notify: (info: { readonly version: string; readonly required: boolean }) => void;
  readonly log: (event: string, detail?: Record<string, unknown>) => void;
  readonly firstCheckDelayMs?: number;
  readonly checkIntervalMs?: number;
  /** Fetch the package as soon as a version is found (default true). */
  readonly autoPrepare?: boolean;
}

export interface UpdatesHandle {
  start(): void;
  stop(): void;
  checkNow(reason: CheckReason): Promise<UpdateState>;
  install(): Promise<UpdateState>;
  state(): UpdateState;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function createUpdates(deps: UpdatesDeps): UpdatesHandle {
  const { log } = deps;
  const installer = deps.installer;
  const firstDelay = deps.firstCheckDelayMs ?? UPDATE_FIRST_CHECK_DELAY_MS;
  const interval = deps.checkIntervalMs ?? UPDATE_CHECK_INTERVAL_MS;
  const autoPrepare = deps.autoPrepare ?? true;

  let state: UpdateState = {
    current: deps.currentVersion,
    platform: deps.platform,
    supported: installer !== null,
    phase: "idle",
    latest: null,
    required: false,
    downloadUrl: null,
    progress: null,
    error: null,
    lastCheckedAt: null,
  };
  let release: ReleaseInfo | null = null;
  const notified = new Set<string>();
  let preparing: Promise<void> | null = null;
  let cancelFirst: (() => void) | null = null;
  let cancelPeriodic: (() => void) | null = null;

  const set = (patch: Partial<UpdateState>): void => {
    state = { ...state, ...patch };
    deps.onState(state);
  };

  const prepare = async (): Promise<boolean> => {
    if (installer === null || release === null || release.latest === null) return false;
    if (preparing !== null) {
      await preparing;
      return state.phase === "ready";
    }
    const version = release.latest;
    set({ phase: "downloading", progress: 0, error: null });
    preparing = installer
      .prepare(release, (percent) => {
        if (state.phase === "downloading") {
          set({ progress: Math.max(0, Math.min(100, Math.round(percent))) });
        }
      })
      .then(
        () => {
          set({ phase: "ready", progress: 100, error: null });
          log("update_ready", { version });
        },
        (err: unknown) => {
          set({ phase: "failed", progress: null, error: errorText(err) });
          log("update_prepare_failed", { version, detail: errorText(err) });
        },
      )
      .finally(() => {
        preparing = null;
      });
    await preparing;
    return state.phase === "ready";
  };

  const checkNow = async (reason: CheckReason): Promise<UpdateState> => {
    if (state.phase === "downloading" || state.phase === "installing") return state;
    const before = state.phase;
    if (reason === "manual") set({ phase: "checking", error: null });
    log("update_check", { reason, current: deps.currentVersion });
    const result = await deps.check({
      currentVersion: deps.currentVersion,
      platform: deps.platform,
    });
    const at = new Date(deps.now()).toISOString();
    if (!result.ok) {
      log("update_check_failed", { reason, status: result.status, detail: result.detail });
      if (reason === "manual") {
        set({
          phase: before === "checking" ? "idle" : before,
          error: `Could not check for updates: ${result.detail}`,
          lastCheckedAt: at,
        });
      } else {
        set({ lastCheckedAt: at });
      }
      return state;
    }

    const rel = result.release;
    release = rel;
    if (rel.status === "update_available" && rel.latest !== null) {
      const fresh = state.latest !== rel.latest;
      if (state.phase === "ready" && !fresh) {
        set({ lastCheckedAt: at });
        return state;
      }
      set({
        phase: "available",
        latest: rel.latest,
        required: rel.required,
        downloadUrl: rel.downloadUrl,
        progress: null,
        error: fresh ? null : state.error,
        lastCheckedAt: at,
      });
      if (!notified.has(rel.latest)) {
        notified.add(rel.latest);
        log("update_available", { version: rel.latest, required: rel.required });
        deps.notify({ version: rel.latest, required: rel.required });
      }
      if (autoPrepare && installer !== null && (fresh || before !== "failed")) void prepare();
    } else if (rel.status === "up_to_date") {
      set({
        phase: "up_to_date",
        latest: rel.latest,
        required: false,
        downloadUrl: rel.downloadUrl,
        progress: null,
        error: null,
        lastCheckedAt: at,
      });
    } else {
      set({
        phase: "unknown",
        latest: null,
        required: false,
        downloadUrl: rel.downloadUrl !== "" ? rel.downloadUrl : state.downloadUrl,
        progress: null,
        error: reason === "manual" ? "The server could not read the release feed." : null,
        lastCheckedAt: at,
      });
    }
    return state;
  };

  const install = async (): Promise<UpdateState> => {
    if (release === null || state.latest === null) return state;
    if (installer === null) {
      set({
        phase: "failed",
        error: "This build cannot update itself — use the download link.",
      });
      return state;
    }
    if (state.phase === "installing") return state;
    if (state.phase !== "ready") {
      const ok = await prepare();
      if (!ok) return state;
    }
    set({ phase: "installing", error: null });
    try {
      await installer.install(release);
    } catch (err) {
      set({ phase: "failed", error: errorText(err) });
      log("update_install_failed", { detail: errorText(err) });
    }
    return state;
  };

  return {
    start() {
      if (cancelFirst !== null || cancelPeriodic !== null) return;
      // Scheduler.every is the only timer seam; a first tick that cancels
      // itself is a one-shot on it.
      cancelFirst = deps.scheduler.every(firstDelay, () => {
        cancelFirst?.();
        cancelFirst = null;
        void checkNow("startup");
      });
      cancelPeriodic = deps.scheduler.every(interval, () => void checkNow("periodic"));
    },
    stop() {
      cancelFirst?.();
      cancelFirst = null;
      cancelPeriodic?.();
      cancelPeriodic = null;
    },
    checkNow,
    install,
    state: () => state,
  };
}
