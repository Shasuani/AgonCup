/**
 * Platform installers (node:test; fake electron-updater + fake disk, no Electron,
 * no network, no Mac). Pins: the Windows strategy downloads once and installs
 * silently; the macOS strategy verifies the checksum, refuses read-only copies,
 * and hands a correct swap script to a detached shell.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReleaseInfo } from "./check.js";
import {
  appBundleFromExecPath,
  buildMacSwapScript,
  electronUpdaterInstaller,
  macSwapInstaller,
  type MacSwapDeps,
  type UpdaterLike,
} from "./installers.js";

const release = (over: Partial<ReleaseInfo> = {}): ReleaseInfo => ({
  platform: "darwin",
  current: "0.1.0",
  latest: "0.2.0",
  minimum: null,
  status: "update_available",
  upToDate: false,
  required: false,
  publishedAt: null,
  downloadUrl: "https://x/downloads/LeagueOfLeagues.dmg",
  packageUrl: "https://x/downloads/LeagueOfLeagues-0.2.0-universal.zip",
  packageSha512: "good==",
  packageSize: 10,
  feedUrl: "https://x/downloads",
  ...over,
});

const noLog = (): void => {};

// --- helpers --------------------------------------------------------------

test("appBundleFromExecPath: finds the .app, null outside a bundle", () => {
  assert.equal(
    appBundleFromExecPath("/Applications/LeagueOfLeagues.app/Contents/MacOS/LeagueOfLeagues"),
    "/Applications/LeagueOfLeagues.app",
  );
  assert.equal(appBundleFromExecPath("/usr/local/bin/electron"), null);
});

test("buildMacSwapScript: waits on the pid, swaps with rollback, clears quarantine, relaunches", () => {
  const script = buildMacSwapScript({
    pid: 4242,
    appPath: "/Applications/League's.app",
    newAppPath: "/tmp/stage/app/LeagueOfLeagues.app",
  });
  assert.ok(script.startsWith("#!/bin/sh\n"));
  assert.ok(script.includes("PID=4242"));
  assert.ok(script.includes(`while kill -0 "$PID"`));
  // single quotes inside paths are escaped for sh
  assert.ok(script.includes(`'/Applications/League'\\''s.app'`));
  assert.ok(script.includes("mv '/tmp/stage/app/LeagueOfLeagues.app' '/Applications/League'\\''s.app'"));
  assert.ok(script.includes(".old'"));
  assert.ok(script.includes("xattr -dr com.apple.quarantine"));
  assert.ok(script.trimEnd().endsWith("open -n '/Applications/League'\\''s.app'"));
});

// --- Windows: electron-updater ---------------------------------------------

function fakeUpdater(feedVersion: string | null) {
  const listeners = new Map<string, Array<(arg: never) => void>>();
  const u = {
    autoDownload: true,
    autoInstallOnAppQuit: false,
    feed: null as string | null,
    checks: 0,
    downloads: 0,
    installs: [] as Array<[boolean | undefined, boolean | undefined]>,
    setFeedURL(o: { provider: "generic"; url: string }) {
      u.feed = o.url;
    },
    on(event: string, fn: (arg: never) => void) {
      listeners.set(event, [...(listeners.get(event) ?? []), fn]);
      return u;
    },
    off(event: string, fn: (arg: never) => void) {
      listeners.set(event, (listeners.get(event) ?? []).filter((x) => x !== fn));
      return u;
    },
    async checkForUpdates() {
      u.checks += 1;
      return feedVersion === null ? null : { updateInfo: { version: feedVersion } };
    },
    async downloadUpdate() {
      u.downloads += 1;
      for (const fn of listeners.get("download-progress") ?? []) fn({ percent: 55 } as never);
      return ["file"];
    },
    quitAndInstall(a?: boolean, b?: boolean) {
      u.installs.push([a, b]);
    },
    progressListeners: () => (listeners.get("download-progress") ?? []).length,
  };
  return u as typeof u & UpdaterLike;
}

test("electronUpdaterInstaller: configures the feed, downloads once, installs silently", async () => {
  const u = fakeUpdater("0.2.0");
  const inst = electronUpdaterInstaller({ updater: u, feedUrl: "https://x/downloads", currentVersion: "0.1.0", log: noLog });
  assert.equal(u.feed, "https://x/downloads");
  assert.equal(u.autoDownload, false);
  assert.equal(u.autoInstallOnAppQuit, true);

  await assert.rejects(inst.install(release()), /not been downloaded/);

  const progress: number[] = [];
  await inst.prepare(release(), (p) => progress.push(p));
  assert.equal(u.checks, 1);
  assert.equal(u.downloads, 1);
  assert.deepEqual(progress, [55]);
  assert.equal(u.progressListeners(), 0, "progress listener detached after the download");

  await inst.prepare(release(), (p) => progress.push(p)); // same version: no second download
  assert.equal(u.downloads, 1);
  assert.deepEqual(progress, [55, 100]);

  await inst.install(release());
  assert.deepEqual(u.installs, [[true, true]]);
});

test("electronUpdaterInstaller: a feed that does not list the new version yet fails prepare", async () => {
  const u = fakeUpdater("0.1.0");
  const inst = electronUpdaterInstaller({ updater: u, feedUrl: "f", currentVersion: "0.1.0", log: noLog });
  await assert.rejects(inst.prepare(release(), () => {}), /does not list 0\.2\.0 yet/);
  assert.equal(u.downloads, 0);
});

// --- macOS: swap ------------------------------------------------------------

function fakeMac(over: Partial<MacSwapDeps> = {}) {
  const calls: string[] = [];
  const files = new Map<string, string>();
  const deps: MacSwapDeps = {
    appBundlePath: "/Applications/LeagueOfLeagues.app",
    stagingDir: "/tmp/stage",
    pid: 77,
    download: async (url, dest, onProgress) => {
      calls.push(`download ${url} -> ${dest}`);
      onProgress(50);
    },
    sha512Base64: async () => "good==",
    extractZip: async (zip, dir) => {
      calls.push(`extract ${zip} -> ${dir}`);
    },
    findAppBundle: async (dir) => `${dir}/LeagueOfLeagues.app`,
    isWritable: async () => true,
    writeFile: async (p, text) => {
      files.set(p, text);
    },
    chmod: async (p, mode) => {
      calls.push(`chmod ${p} ${mode.toString(8)}`);
    },
    rm: async (p) => {
      calls.push(`rm ${p}`);
    },
    mkdir: async (p) => {
      calls.push(`mkdir ${p}`);
    },
    spawnDetached: (p) => {
      calls.push(`spawn ${p}`);
    },
    quit: () => {
      calls.push("quit");
    },
    log: noLog,
    ...over,
  };
  return { deps, calls, files };
}

test("macSwapInstaller: download, verify, unpack, then swap script + quit", async () => {
  const { deps, calls, files } = fakeMac();
  const inst = macSwapInstaller(deps);
  const progress: number[] = [];
  await inst.prepare(release(), (p) => progress.push(p));
  assert.deepEqual(calls, [
    "rm /tmp/stage",
    "mkdir /tmp/stage",
    "download https://x/downloads/LeagueOfLeagues-0.2.0-universal.zip -> /tmp/stage/update.zip",
    "mkdir /tmp/stage/app",
    "extract /tmp/stage/update.zip -> /tmp/stage/app",
  ]);
  assert.deepEqual(progress, [50, 100]);

  calls.length = 0;
  await inst.install(release());
  const script = files.get("/tmp/stage/swap.sh");
  assert.ok(script?.includes("PID=77"));
  assert.ok(script?.includes("mv '/tmp/stage/app/LeagueOfLeagues.app' '/Applications/LeagueOfLeagues.app'"));
  assert.deepEqual(calls, ["chmod /tmp/stage/swap.sh 755", "spawn /tmp/stage/swap.sh", "quit"]);
});

test("macSwapInstaller: checksum mismatch discards the download", async () => {
  const { deps, calls } = fakeMac({ sha512Base64: async () => "bad==" });
  const inst = macSwapInstaller(deps);
  await assert.rejects(inst.prepare(release(), () => {}), /integrity check/);
  assert.ok(calls.filter((c) => c === "rm /tmp/stage").length === 2, "staging wiped again after the mismatch");
  await assert.rejects(inst.install(release()), /not been downloaded/);
});

test("macSwapInstaller: no package on the feed, or a read-only copy, never spawns the swap", async () => {
  const noPkg = macSwapInstaller(fakeMac().deps);
  await assert.rejects(noPkg.prepare(release({ packageUrl: null }), () => {}), /No update package/);

  const ro = fakeMac({ appBundlePath: "/Volumes/LeagueOfLeagues/LeagueOfLeagues.app" });
  const inst = macSwapInstaller(ro.deps);
  await inst.prepare(release(), () => {});
  await assert.rejects(inst.install(release()), /cannot be replaced/);
  assert.ok(!ro.calls.some((c) => c.startsWith("spawn")));
  assert.ok(!ro.calls.includes("quit"));

  const notWritable = fakeMac({ isWritable: async () => false });
  const inst2 = macSwapInstaller(notWritable.deps);
  await inst2.prepare(release(), () => {});
  await assert.rejects(inst2.install(release()), /cannot be replaced/);
});
