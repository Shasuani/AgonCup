/**
 * Auto-update wiring: electron-updater over a plain HTTPS directory.
 *
 * The feed is `<backend origin>/downloads/` — the same files the website's
 * download buttons point at, put there by `infra/publish-desktop.sh`, no
 * release service in between. electron-updater reads `latest.yml` /
 * `latest-mac.yml` from it, compares against the running version, and:
 *
 *   - **Windows** downloads the new installer in the background and asks to
 *     restart. "Later" is honoured — it installs when the app next quits — so a
 *     player mid-match is never yanked out of a lobby.
 *   - **macOS** only CHECKS. Squirrel.Mac refuses to swap in an unsigned bundle,
 *     and the client is unsigned until the LCU-write ToS spike clears the beta
 *     gate (docs/compliance-checklist.md), so the player is sent to the .dmg on
 *     the site instead of watching a download fail.
 *
 * Injected seams (the updater, the prompts, the scheduler) keep this unit-
 * testable with no Electron: `main.ts` supplies the real electron-updater
 * instance and native dialogs. Every updater error is logged and swallowed — an
 * update that cannot be fetched must never take down a client that is running
 * a tournament.
 */

import type { Scheduler } from "./orchestration/index.js";

/** Stable installer names the website links at; `infra/publish-desktop.sh`
 * keeps them pointed at the newest versioned build. */
export const MAC_INSTALLER_FILE = "LeagueOfLeagues.dmg";
export const WINDOWS_INSTALLER_FILE = "LeagueOfLeagues-Setup.exe";

/** How often a running client re-reads the feed. Tournament clients stay open
 * for hours, so a boot-only check would miss a same-day fix. */
export const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1_000;

/** Grace before the first check, so it never races the boot refresh + window. */
export const UPDATE_FIRST_CHECK_DELAY_MS = 15_000;

/** What an update looks like to this module (electron-updater's UpdateInfo is wider). */
export interface UpdateInfoLike {
  readonly version: string;
}

/** The slice of electron-updater's `AppUpdater` this module drives. */
export interface UpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  setFeedURL(options: { provider: "generic"; url: string }): void;
  on(event: "update-available", listener: (info: UpdateInfoLike) => void): unknown;
  on(event: "update-downloaded", listener: (info: UpdateInfoLike) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
  checkForUpdates(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface AutoUpdateDeps {
  readonly updater: UpdaterLike;
  /** `process.platform`; anything but win32/darwin gets no updater at all. */
  readonly platform: string;
  /** `<origin>/downloads` — the feed directory (`ServerUrls.downloadsUrl`). */
  readonly downloadsUrl: string;
  readonly scheduler: Scheduler;
  /** Windows: "restart now?" once the installer is downloaded. */
  readonly askRestart: (version: string) => Promise<boolean>;
  /** macOS: "open the download?" when a newer version is on the feed. */
  readonly askDownload: (version: string) => Promise<boolean>;
  /** macOS: hand the URL to the browser. */
  readonly openExternal: (url: string) => void;
  readonly log: (event: string, detail?: Record<string, unknown>) => void;
  /** First-check delay + recheck cadence; defaults are the module constants. */
  readonly firstCheckDelayMs?: number;
  readonly checkIntervalMs?: number;
}

export interface AutoUpdateHandle {
  /** Begin: one check after the grace period, then every interval. */
  start(): void;
  /** Cancel the timers (app shutdown). Listeners stay attached; harmless. */
  stop(): void;
  /** Run a check now (also what the timers call). Never throws. */
  checkNow(): Promise<void>;
}

/**
 * Wire the platform's update behaviour onto `deps.updater`.
 *
 * Prompts fire at most once per version per process: electron-updater re-emits
 * `update-available` on every check, and a 4-hourly "download the update?" on
 * a player who already said "later" is how prompts get reflexively dismissed.
 */
export function wireAutoUpdate(deps: AutoUpdateDeps): AutoUpdateHandle {
  const { updater, platform, log } = deps;
  const isWindows = platform === "win32";
  const isMac = platform === "darwin";
  const firstDelay = deps.firstCheckDelayMs ?? UPDATE_FIRST_CHECK_DELAY_MS;
  const interval = deps.checkIntervalMs ?? UPDATE_CHECK_INTERVAL_MS;

  let cancelFirst: (() => void) | null = null;
  let cancelPeriodic: (() => void) | null = null;
  /** Versions the user has already been asked about this session. */
  const prompted = new Set<string>();

  const checkNow = async (): Promise<void> => {
    if (!isWindows && !isMac) return;
    try {
      log("update_check", { feed: deps.downloadsUrl });
      await updater.checkForUpdates();
    } catch (err) {
      log("update_check_failed", { detail: String(err) });
    }
  };

  const handle: AutoUpdateHandle = {
    start() {
      if (!isWindows && !isMac) {
        log("update_unsupported_platform", { platform });
        return;
      }
      if (cancelFirst !== null || cancelPeriodic !== null) return;
      // Scheduler.every is the only timer seam; a first tick that cancels
      // itself is a one-shot on it.
      cancelFirst = deps.scheduler.every(firstDelay, () => {
        cancelFirst?.();
        cancelFirst = null;
        void checkNow();
      });
      cancelPeriodic = deps.scheduler.every(interval, () => void checkNow());
    },
    stop() {
      cancelFirst?.();
      cancelFirst = null;
      cancelPeriodic?.();
      cancelPeriodic = null;
    },
    checkNow,
  };

  if (!isWindows && !isMac) return handle;

  updater.setFeedURL({ provider: "generic", url: deps.downloadsUrl });
  // Windows fetches the installer as soon as one is found; macOS never
  // downloads (see the module doc — it could not install what it fetched).
  updater.autoDownload = isWindows;
  updater.autoInstallOnAppQuit = isWindows;

  updater.on("error", (error) => {
    log("update_error", { detail: String(error) });
  });

  updater.on("update-available", (info) => {
    log("update_available", { version: info.version, platform });
    if (!isMac || prompted.has(info.version)) return;
    prompted.add(info.version);
    void deps.askDownload(info.version).then((yes) => {
      if (!yes) return;
      deps.openExternal(`${deps.downloadsUrl}/${MAC_INSTALLER_FILE}`);
      log("update_download_opened", { version: info.version });
    });
  });

  updater.on("update-downloaded", (info) => {
    log("update_downloaded", { version: info.version });
    if (!isWindows || prompted.has(info.version)) return;
    prompted.add(info.version);
    void deps.askRestart(info.version).then((yes) => {
      if (!yes) {
        // autoInstallOnAppQuit finishes the job when the player closes the app.
        log("update_deferred", { version: info.version });
        return;
      }
      log("update_installing", { version: info.version });
      // Silent so the NSIS wizard is not shown again; relaunch when done.
      updater.quitAndInstall(true, true);
    });
  });

  return handle;
}
