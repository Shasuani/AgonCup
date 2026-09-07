/**
 * How an update gets onto the disk and running — one strategy per platform,
 * behind a single {@link Installer} interface the coordinator drives:
 *
 *   - **Windows** — electron-updater: `checkForUpdates()` against the site's
 *     `/downloads` feed, `downloadUpdate()` (NSIS installer + blockmap delta),
 *     then `quitAndInstall(silent, runAfter)` swaps the install and relaunches.
 *     `autoInstallOnAppQuit` stays on, so a player who chose "Later" still gets
 *     the update when they close the app.
 *   - **macOS** — our own swap. Squirrel.Mac refuses to update an UNSIGNED bundle
 *     and the client is unsigned until the ToS spike clears the beta gate
 *     (docs/compliance-checklist.md), so: download the universal zip named in
 *     `latest-mac.yml`, verify its SHA-512 against the feed, unpack it with
 *     `ditto` (keeps frameworks/symlinks intact), then hand a tiny shell script
 *     the job of waiting for this process to exit, moving the new bundle over the
 *     old one and relaunching it. Everything touching the disk is injected, so
 *     the flow is unit-tested without a Mac.
 *
 * Neither strategy is loaded for `electron .` (nothing to update) — the
 * coordinator then only offers the manual download link.
 */

import { join } from "node:path";

import type { ReleaseInfo } from "./check.js";
import { isNewer } from "./version.js";

/** Stable installer names the website links at; `infra/publish-desktop.sh`
 * keeps them pointed at the newest versioned build. Must match
 * `services/backend/src/app/desktop/service.py`. */
export const MAC_INSTALLER_FILE = "LeagueOfLeagues.dmg";
export const WINDOWS_INSTALLER_FILE = "LeagueOfLeagues-Setup.exe";

export type Logger = (event: string, detail?: Record<string, unknown>) => void;

export interface Installer {
  readonly kind: "electron-updater" | "mac-swap";
  /** Fetch + stage `release` so `install()` is instant. Progress is 0..100. */
  prepare(release: ReleaseInfo, onProgress: (percent: number) => void): Promise<void>;
  /** Swap in the staged build and relaunch; the app quits from inside this. */
  install(release: ReleaseInfo): Promise<void>;
}

// ---------------------------------------------------------------------------
// Windows: electron-updater
// ---------------------------------------------------------------------------

/** The slice of electron-updater's `AppUpdater` this module drives. */
export interface UpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  setFeedURL(options: { provider: "generic"; url: string }): void;
  on(event: "download-progress", listener: (progress: { percent: number }) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
  off(event: "download-progress", listener: (progress: { percent: number }) => void): unknown;
  checkForUpdates(): Promise<{ updateInfo: { version: string } } | null>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface ElectronUpdaterDeps {
  readonly updater: UpdaterLike;
  /** `<origin>/downloads` — the feed directory. */
  readonly feedUrl: string;
  readonly currentVersion: string;
  readonly log: Logger;
}

export function electronUpdaterInstaller(deps: ElectronUpdaterDeps): Installer {
  const { updater, log } = deps;
  updater.setFeedURL({ provider: "generic", url: deps.feedUrl });
  // The coordinator decides WHEN to download (it reports progress to the UI);
  // a downloaded update still installs on quit if the player never presses Restart.
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = true;
  updater.on("error", (error) => log("update_error", { detail: String(error) }));

  let downloaded: string | null = null;

  return {
    kind: "electron-updater",
    async prepare(release, onProgress) {
      if (downloaded !== null && downloaded === release.latest) {
        onProgress(100);
        return;
      }
      const result = await updater.checkForUpdates();
      const version = result?.updateInfo?.version;
      if (version === undefined || !isNewer(version, deps.currentVersion)) {
        throw new Error(
          `The update feed does not list ${release.latest ?? "the new version"} yet — try again in a few minutes.`,
        );
      }
      const onDownloadProgress = (p: { percent: number }): void => onProgress(p.percent);
      updater.on("download-progress", onDownloadProgress);
      try {
        await updater.downloadUpdate();
      } finally {
        updater.off("download-progress", onDownloadProgress);
      }
      downloaded = version;
      log("update_downloaded", { version });
    },
    async install() {
      if (downloaded === null) throw new Error("The update has not been downloaded yet.");
      log("update_installing", { version: downloaded });
      // Silent so the NSIS wizard is not shown again; relaunch when done.
      updater.quitAndInstall(true, true);
    },
  };
}

// ---------------------------------------------------------------------------
// macOS: download → verify → unpack → swap script → relaunch
// ---------------------------------------------------------------------------

export interface MacSwapDeps {
  /** `/Applications/LeagueOfLeagues.app` — see {@link appBundleFromExecPath}. */
  readonly appBundlePath: string;
  /** Scratch directory owned by this updater (wiped on every prepare). */
  readonly stagingDir: string;
  /** This process, for the swap script to wait on. */
  readonly pid: number;
  readonly download: (
    url: string,
    dest: string,
    onProgress: (percent: number) => void,
  ) => Promise<void>;
  readonly sha512Base64: (file: string) => Promise<string>;
  readonly extractZip: (zip: string, destDir: string) => Promise<void>;
  /** The `.app` directly inside `dir`, or null. */
  readonly findAppBundle: (dir: string) => Promise<string | null>;
  readonly isWritable: (path: string) => Promise<boolean>;
  readonly writeFile: (path: string, text: string) => Promise<void>;
  readonly chmod: (path: string, mode: number) => Promise<void>;
  readonly rm: (path: string) => Promise<void>;
  readonly mkdir: (path: string) => Promise<void>;
  /** Start the swap script detached from this process (it outlives us). */
  readonly spawnDetached: (scriptPath: string) => void;
  readonly quit: () => void;
  readonly log: Logger;
}

/** `/Applications/X.app/Contents/MacOS/X` → `/Applications/X.app`; null when not in a bundle. */
export function appBundleFromExecPath(execPath: string): string | null {
  const parts = execPath.split("/");
  const idx = parts.findIndex((p) => p.endsWith(".app"));
  return idx === -1 ? null : parts.slice(0, idx + 1).join("/");
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The script that finishes a macOS update after this process exits.
 *
 * It waits (up to a minute) for the running app to go away, moves the old
 * bundle aside, moves the new one in, puts the old one back if that failed,
 * clears the quarantine flag the fresh bundle would otherwise carry (this is
 * the player's own already-installed app updating itself), and relaunches.
 */
export function buildMacSwapScript(args: {
  readonly pid: number;
  readonly appPath: string;
  readonly newAppPath: string;
}): string {
  const app = shellQuote(args.appPath);
  const next = shellQuote(args.newAppPath);
  const old = shellQuote(`${args.appPath}.old`);
  return [
    "#!/bin/sh",
    "# LeagueOfLeagues self-update: swap the bundle once the app has quit, then relaunch.",
    `PID=${Math.trunc(args.pid)}`,
    "i=0",
    'while kill -0 "$PID" 2>/dev/null; do',
    "  sleep 0.5",
    "  i=$((i+1))",
    '  [ "$i" -gt 120 ] && break',
    "done",
    `rm -rf ${old}`,
    `if [ -e ${app} ]; then mv ${app} ${old} || exit 1; fi`,
    `if mv ${next} ${app}; then`,
    `  rm -rf ${old}`,
    "else",
    `  [ -e ${old} ] && mv ${old} ${app}`,
    "  exit 1",
    "fi",
    `xattr -dr com.apple.quarantine ${app} 2>/dev/null || true`,
    `open -n ${app}`,
    "",
  ].join("\n");
}

export function macSwapInstaller(deps: MacSwapDeps): Installer {
  const { log } = deps;
  let staged: { readonly version: string; readonly appPath: string } | null = null;

  return {
    kind: "mac-swap",
    async prepare(release, onProgress) {
      if (staged !== null && staged.version === release.latest) {
        onProgress(100);
        return;
      }
      if (release.packageUrl === null || release.latest === null) {
        throw new Error("No update package is published for macOS yet.");
      }
      staged = null;
      await deps.rm(deps.stagingDir);
      await deps.mkdir(deps.stagingDir);
      const zip = join(deps.stagingDir, "update.zip");
      await deps.download(release.packageUrl, zip, onProgress);
      if (release.packageSha512 !== null) {
        const got = await deps.sha512Base64(zip);
        if (got !== release.packageSha512) {
          await deps.rm(deps.stagingDir);
          log("update_checksum_mismatch", { version: release.latest });
          throw new Error("The downloaded update failed its integrity check.");
        }
      }
      const unpacked = join(deps.stagingDir, "app");
      await deps.mkdir(unpacked);
      await deps.extractZip(zip, unpacked);
      const appPath = await deps.findAppBundle(unpacked);
      if (appPath === null) throw new Error("The update package did not contain the app.");
      staged = { version: release.latest, appPath };
      onProgress(100);
      log("update_staged", { version: release.latest, appPath });
    },
    async install() {
      if (staged === null) throw new Error("The update has not been downloaded yet.");
      const target = deps.appBundlePath;
      // A copy still running from the mounted .dmg (or another read-only place)
      // cannot be replaced: the manual installer is the way, and the UI offers it.
      if (target.startsWith("/Volumes/") || !(await deps.isWritable(target))) {
        throw new Error(
          `This copy runs from ${target}, which cannot be replaced from inside the app. Open the download and drag it into Applications.`,
        );
      }
      const script = buildMacSwapScript({
        pid: deps.pid,
        appPath: target,
        newAppPath: staged.appPath,
      });
      const scriptPath = join(deps.stagingDir, "swap.sh");
      await deps.writeFile(scriptPath, script);
      await deps.chmod(scriptPath, 0o755);
      log("update_installing", { version: staged.version, target });
      deps.spawnDetached(scriptPath);
      deps.quit();
    },
  };
}
