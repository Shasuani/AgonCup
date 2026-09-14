/**
 * How an update gets onto the disk and running — one strategy per platform,
 * behind a single {@link Installer} interface the coordinator drives:
 *
 *   - **Windows** — electron-updater: `checkForUpdates()` against the site's
 *     `/downloads` feed, `downloadUpdate()` (the full NSIS installer — no
 *     blockmaps are published, so differential download is switched off),
 *     then `quitAndInstall(silent, runAfter)` swaps the install and relaunches.
 *     `autoInstallOnAppQuit` stays on, so a player who chose "Later" still gets
 *     the update when they close the app.
 *   - **macOS** — our own swap. Squirrel.Mac refuses to update an UNSIGNED bundle
 *     and the client is unsigned until the ToS spike clears the beta gate
 *     (docs/compliance-checklist.md), so: download the universal zip named in
 *     `latest-mac.yml`, verify its SHA-512 against the feed, unpack it with
 *     `ditto` (keeps frameworks/symlinks intact), then hand a tiny shell script
 *     the job of waiting for this process to exit, moving the new bundle into
 *     place and relaunching it. Everything touching the disk is injected, so
 *     the flow is unit-tested without a Mac.
 *
 * The player only ever presses "Update": neither strategy may end in "now do
 * something by hand". A copy started straight from the .dmg (or one macOS
 * translocated) is installed into Applications; a swap that fails reopens the
 * copy that was running; an installer that never starts is reported instead of
 * leaving the banner on "Restarting…". Each strategy also clears what the
 * previous update left on disk ({@link Installer.cleanup}, called at boot).
 *
 * Neither strategy is loaded for `electron .` (nothing to update) — the
 * coordinator then only offers the manual download link.
 */

import { basename, join } from "node:path";

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
  /**
   * Delete what an earlier update left behind (the downloaded package, the
   * unpacked bundle, the swap script). Run once at boot, before any check;
   * never throws.
   */
  cleanup(): Promise<void>;
}

const defaultWait = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// Windows: electron-updater
// ---------------------------------------------------------------------------

/** The slice of electron-updater's `AppUpdater` this module drives. */
export interface UpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  disableDifferentialDownload: boolean;
  setFeedURL(options: { provider: "generic"; url: string }): void;
  on(event: "download-progress", listener: (progress: { percent: number }) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
  off(event: "download-progress", listener: (progress: { percent: number }) => void): unknown;
  off(event: "error", listener: (error: Error) => void): unknown;
  checkForUpdates(): Promise<{ updateInfo: { version: string } } | null>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

/**
 * How long `install()` gives the app to quit into the installer. The quit is
 * immediate when the installer starts, so still being alive this long after
 * means it did not (electron-updater reports that only on its `error` event).
 */
export const WINDOWS_INSTALL_START_TIMEOUT_MS = 30_000;

export interface ElectronUpdaterDeps {
  readonly updater: UpdaterLike;
  /** `<origin>/downloads` — the feed directory. */
  readonly feedUrl: string;
  readonly currentVersion: string;
  /**
   * electron-updater's download cache (`%LOCALAPPDATA%\<updaterCacheDirName>`),
   * whose `pending` folder keeps the ~100 MB installer after it has run. Null
   * when unknown: nothing is cleaned.
   */
  readonly cacheDir: string | null;
  /** Recursive, force delete. */
  readonly rm: (path: string) => Promise<void>;
  readonly log: Logger;
  readonly wait?: (ms: number) => Promise<void>;
  readonly installStartTimeoutMs?: number;
}

export function electronUpdaterInstaller(deps: ElectronUpdaterDeps): Installer {
  const { updater, log } = deps;
  const wait = deps.wait ?? defaultWait;
  const startTimeout = deps.installStartTimeoutMs ?? WINDOWS_INSTALL_START_TIMEOUT_MS;
  updater.setFeedURL({ provider: "generic", url: deps.feedUrl });
  // The coordinator decides WHEN to download (it reports progress to the UI);
  // a downloaded update still installs on quit if the player never presses Restart.
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = true;
  // The installer keeps the same file name across versions (electron-builder.yml)
  // and no .blockmap is published, so a delta download has nothing to work with:
  // skip the attempt instead of logging a 404 before every full download.
  updater.disableDifferentialDownload = true;
  updater.on("error", (error) => log("update_error", { detail: String(error) }));

  let downloaded: string | null = null;
  let downloadedThisRun = false;

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
      downloadedThisRun = true;
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
      // electron-updater quits the app as soon as the installer is spawned; when
      // it cannot start one it only emits `error` and the app stays open. Wait
      // for either, so the banner never sits on "Restarting…" for good.
      let onError: (error: Error) => void = () => {};
      const failed = new Promise<Error>((resolve) => {
        onError = resolve;
      });
      updater.on("error", onError);
      try {
        // Silent so the NSIS wizard is not shown again; relaunch when done.
        updater.quitAndInstall(true, true);
        const outcome = await Promise.race([failed, wait(startTimeout).then(() => null)]);
        log("update_install_did_not_start", { detail: outcome === null ? "timeout" : String(outcome) });
        throw new Error(
          outcome === null
            ? "The installer did not start. Use “Download manually” to install the new version."
            : `The installer could not start (${outcome.message}). Use “Download manually” to install the new version.`,
        );
      } finally {
        updater.off("error", onError);
      }
    },
    async cleanup() {
      // Never under a download of this run: `pending` is where it lives until
      // the installer runs (on "Restart now", or on quit for "Later").
      if (deps.cacheDir === null || downloadedThisRun) return;
      try {
        await deps.rm(join(deps.cacheDir, "pending"));
      } catch (err) {
        log("update_cleanup_failed", { detail: errorText(err) });
      }
    },
  };
}

// ---------------------------------------------------------------------------
// macOS: download → verify → unpack → swap script → relaunch
// ---------------------------------------------------------------------------

export interface MacSwapDeps {
  /** The bundle this process runs from — see {@link appBundleFromExecPath}. */
  readonly appBundlePath: string;
  /**
   * Where that bundle really lives when macOS translocated it (it then runs
   * from a read-only `/private/var/folders/…/AppTranslocation/…` mirror) — see
   * {@link originalBundleFromMounts}. Null when not translocated or unknown.
   */
  readonly originalBundlePath: string | null;
  /**
   * Where a copy that cannot be updated in place gets installed instead, in
   * order of preference: `/Applications`, then `~/Applications` (for accounts
   * that may not write to /Applications).
   */
  readonly applicationsDirs: readonly string[];
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
  /**
   * Could this process put a bundle at `path`? True when `path` (if it exists)
   * is writable and so is the nearest existing folder above it.
   */
  readonly canReplace: (path: string) => Promise<boolean>;
  readonly writeFile: (path: string, text: string) => Promise<void>;
  /** A small text file's content, or null when it does not exist. */
  readonly readText: (path: string) => Promise<string | null>;
  readonly chmod: (path: string, mode: number) => Promise<void>;
  /**
   * Recursive, force delete. MUST see through Electron's asar virtualisation
   * (`original-fs`, not `fs`): the staging dir holds a whole app bundle whose
   * `app.asar` the patched `fs.rm` skips, which then fails with ENOTEMPTY.
   */
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

/** A path macOS App Translocation serves (read-only; the real bundle is elsewhere). */
export function isTranslocated(bundlePath: string): boolean {
  return bundlePath.includes("/AppTranslocation/");
}

/**
 * The real location of a translocated bundle, read from `mount` output.
 *
 * Translocation is a read-only nullfs mount whose SOURCE is the original bundle
 * (e.g. `/Volumes/LeagueOfLeagues/LeagueOfLeagues.app on /private/var/folders/…/
 * AppTranslocation/<UUID> (nullfs, …)`), and the app runs from somewhere under
 * that mount point. Null when `bundlePath` is not under any such mount.
 */
export function originalBundleFromMounts(bundlePath: string, mountOutput: string): string | null {
  for (const line of mountOutput.split("\n")) {
    const m = /^(.+\.app) on (.+\/AppTranslocation\/[^/ ]+) \(/.exec(line);
    if (m === null) continue;
    const [, source, mountPoint] = m;
    if (source !== undefined && mountPoint !== undefined && bundlePath.startsWith(`${mountPoint}/`)) {
      return source;
    }
  }
  return null;
}

/** `/Volumes/<name>` for a bundle on a mounted volume (e.g. the opened .dmg), else null. */
export function volumeOf(bundlePath: string): string | null {
  const m = /^(\/Volumes\/[^/]+)\//.exec(bundlePath);
  return m === null ? null : (m[1] ?? null);
}

export interface MacInstallTarget {
  /** Where the new bundle goes. */
  readonly target: string;
  /** The copy that was running — reopened if the swap fails. */
  readonly previous: string;
  /** The disk image to eject once the app moved out of it, or null. */
  readonly volume: string | null;
}

/**
 * Where the update gets installed.
 *
 * In place when the running copy can be replaced. Otherwise — started straight
 * from the mounted .dmg, translocated with no known origin, or sitting in a
 * folder this account may not write — into the first Applications folder that
 * takes it, replacing an older copy there. Null only when none does.
 */
export async function chooseMacInstallTarget(deps: MacSwapDeps): Promise<MacInstallTarget | null> {
  const previous = deps.originalBundlePath ?? deps.appBundlePath;
  const volume = volumeOf(previous);
  if (volume === null && !isTranslocated(previous) && (await deps.canReplace(previous))) {
    return { target: previous, previous, volume: null };
  }
  const name = basename(previous);
  for (const dir of deps.applicationsDirs) {
    const target = join(dir, name);
    if (await deps.canReplace(target)) return { target, previous, volume };
  }
  return null;
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The script that finishes a macOS update after this process exits.
 *
 * It waits for the running app to go away (asking it to stop after a minute),
 * moves any bundle at the target aside, moves the new one in, clears the
 * quarantine flag the fresh bundle would otherwise carry (this is the player's
 * own already-installed app updating itself), ejects the .dmg the old copy ran
 * from, and opens the new app. When a move fails it puts the old bundle back
 * and reopens the copy that was running, so the player is never left without
 * an app. Everything it does goes to `logPath` (read back at the next boot).
 */
export function buildMacSwapScript(args: {
  readonly pid: number;
  readonly appPath: string;
  readonly newAppPath: string;
  readonly previousAppPath: string;
  readonly volume: string | null;
  readonly logPath: string;
}): string {
  return [
    "#!/bin/sh",
    "# LeagueOfLeagues self-update: swap the bundle once the app has quit, then relaunch.",
    `exec >>${shellQuote(args.logPath)} 2>&1`,
    `PID=${Math.trunc(args.pid)}`,
    `APP=${shellQuote(args.appPath)}`,
    `NEW=${shellQuote(args.newAppPath)}`,
    `OLD=${shellQuote(`${args.appPath}.old`)}`,
    `PREVIOUS=${shellQuote(args.previousAppPath)}`,
    `VOLUME=${shellQuote(args.volume ?? "")}`,
    'echo "$(date) swap start: $NEW -> $APP"',
    "reopen_previous() {",
    '  if [ -e "$APP" ]; then open -n "$APP"; elif [ -e "$PREVIOUS" ]; then open -n "$PREVIOUS"; fi',
    "}",
    "i=0",
    'while kill -0 "$PID" 2>/dev/null; do',
    "  sleep 0.5",
    "  i=$((i+1))",
    '  [ "$i" -eq 120 ] && kill "$PID" 2>/dev/null',
    '  [ "$i" -gt 140 ] && break',
    "done",
    'mkdir -p "$(dirname "$APP")"',
    'rm -rf "$OLD"',
    'if [ -e "$APP" ] && ! mv "$APP" "$OLD"; then',
    '  echo "swap failed: could not move the old app aside"',
    "  reopen_previous",
    "  exit 1",
    "fi",
    'if ! mv "$NEW" "$APP"; then',
    '  echo "swap failed: could not move the new app in"',
    '  if [ -e "$OLD" ]; then rm -rf "$APP"; mv "$OLD" "$APP"; fi',
    "  reopen_previous",
    "  exit 1",
    "fi",
    'rm -rf "$OLD"',
    'xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true',
    // Only ever a disk image — never unmount a real drive the app happened to be on.
    `if [ -n "$VOLUME" ] && hdiutil info | awk -F '\\t' -v v="$VOLUME" '$NF == v { f = 1 } END { exit !f }'; then`,
    '  hdiutil detach "$VOLUME" -quiet || true',
    "fi",
    'echo "$(date) swap done"',
    'open -n "$APP"',
    "",
  ].join("\n");
}

export function macSwapInstaller(deps: MacSwapDeps): Installer {
  const { log } = deps;
  let staged: { readonly version: string; readonly appPath: string } | null = null;
  const logPath = join(deps.stagingDir, "swap.log");

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
      const where = await chooseMacInstallTarget(deps);
      if (where === null) {
        throw new Error(
          "This Mac account may not write to the Applications folder. Use “Download manually” and install the new version with an administrator account.",
        );
      }
      const script = buildMacSwapScript({
        pid: deps.pid,
        appPath: where.target,
        newAppPath: staged.appPath,
        previousAppPath: where.previous,
        volume: where.volume,
        logPath,
      });
      const scriptPath = join(deps.stagingDir, "swap.sh");
      await deps.writeFile(scriptPath, script);
      await deps.chmod(scriptPath, 0o755);
      log("update_installing", {
        version: staged.version,
        target: where.target,
        running: deps.appBundlePath,
        previous: where.previous,
        volume: where.volume,
      });
      deps.spawnDetached(scriptPath);
      deps.quit();
    },
    async cleanup() {
      if (staged !== null) return;
      try {
        const swapLog = await deps.readText(logPath);
        if (swapLog !== null) log("update_swap_log", { text: swapLog.slice(-4_000) });
        await deps.rm(deps.stagingDir);
      } catch (err) {
        log("update_cleanup_failed", { detail: errorText(err) });
      }
    },
  };
}
