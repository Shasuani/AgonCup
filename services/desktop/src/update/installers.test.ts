/**
 * Platform installers (node:test; fake electron-updater + fake disk, no Electron,
 * no network, no Mac, no Windows). Pins: the Windows strategy downloads once,
 * installs silently, reports an installer that never starts and clears its cache;
 * the macOS strategy verifies the checksum, installs a copy started from the .dmg
 * (or translocated) into Applications, hands a correct swap script to a detached
 * shell, and clears the staging folder.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";

import type { ReleaseInfo } from "./check.js";
import {
  appBundleFromExecPath,
  buildMacSwapScript,
  chooseMacInstallTarget,
  electronUpdaterInstaller,
  isTranslocated,
  macSwapInstaller,
  originalBundleFromMounts,
  volumeOf,
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
  downloadUrl: "https://x/downloads/AgonCup.dmg",
  packageUrl: "https://x/downloads/AgonCup-0.2.0-universal.zip",
  packageSha512: "good==",
  packageSize: 10,
  feedUrl: "https://x/downloads",
  ...over,
});

const noLog = (): void => {};

const TRANSLOCATED =
  "/private/var/folders/3q/r5gt/T/AppTranslocation/A6BC74A7-8D81-446B-ACC7-670E41C95B9D/d/AgonCup.app";
// Real `mount` output from a Mac running the app straight from the opened .dmg.
const MOUNTS = [
  "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)",
  "/dev/disk13s1 on /Volumes/AgonCup 0.2.3-universal (hfs, local, nodev, nosuid, read-only, noowners, quarantine, mounted by kostaskolta)",
  "/Volumes/AgonCup 0.2.3-universal/AgonCup.app on /private/var/folders/3q/r5gt/T/AppTranslocation/A6BC74A7-8D81-446B-ACC7-670E41C95B9D (nullfs, local, nodev, nosuid, read-only, noowners, quarantine, nobrowse, mounted by kostaskolta)",
  "/Applications/Other.app on /private/var/folders/3q/r5gt/T/AppTranslocation/0000AAAA-0000-0000-0000-000000000000 (nullfs, local, read-only, nobrowse)",
  "",
].join("\n");

// --- helpers --------------------------------------------------------------

test("appBundleFromExecPath: finds the .app, null outside a bundle", () => {
  assert.equal(
    appBundleFromExecPath("/Applications/AgonCup.app/Contents/MacOS/AgonCup"),
    "/Applications/AgonCup.app",
  );
  assert.equal(appBundleFromExecPath(`${TRANSLOCATED}/Contents/MacOS/AgonCup`), TRANSLOCATED);
  assert.equal(appBundleFromExecPath("/usr/local/bin/electron"), null);
});

test("isTranslocated / volumeOf: recognise a translocated mirror and a mounted volume", () => {
  assert.equal(isTranslocated(TRANSLOCATED), true);
  assert.equal(isTranslocated("/Applications/AgonCup.app"), false);
  assert.equal(volumeOf("/Volumes/AgonCup 0.2.3-universal/AgonCup.app"), "/Volumes/AgonCup 0.2.3-universal");
  assert.equal(volumeOf("/Applications/AgonCup.app"), null);
});

test("originalBundleFromMounts: maps the translocated mirror back to the .dmg copy", () => {
  assert.equal(originalBundleFromMounts(TRANSLOCATED, MOUNTS), "/Volumes/AgonCup 0.2.3-universal/AgonCup.app");
  assert.equal(originalBundleFromMounts("/Applications/AgonCup.app", MOUNTS), null);
  assert.equal(originalBundleFromMounts(TRANSLOCATED, "/dev/disk3s1s1 on / (apfs)\n"), null);
});

test("buildMacSwapScript: waits on the pid, swaps with rollback, clears quarantine, ejects, relaunches", () => {
  const script = buildMacSwapScript({
    pid: 4242,
    appPath: "/Applications/League's.app",
    newAppPath: "/tmp/stage/app/AgonCup.app",
    previousAppPath: "/Volumes/AgonCup/AgonCup.app",
    volume: "/Volumes/AgonCup",
    logPath: "/tmp/stage/swap.log",
  });
  assert.ok(script.startsWith("#!/bin/sh\n"));
  assert.ok(script.includes("exec >>'/tmp/stage/swap.log' 2>&1"));
  assert.ok(script.includes("PID=4242"));
  assert.ok(script.includes(`while kill -0 "$PID"`));
  // single quotes inside paths are escaped for sh
  assert.ok(script.includes(`APP='/Applications/League'\\''s.app'`));
  assert.ok(script.includes(`OLD='/Applications/League'\\''s.app.old'`));
  assert.ok(script.includes(`NEW='/tmp/stage/app/AgonCup.app'`));
  assert.ok(script.includes(`VOLUME='/Volumes/AgonCup'`));
  assert.ok(script.includes('mv "$NEW" "$APP"'));
  assert.ok(script.includes("reopen_previous"));
  assert.ok(script.includes("xattr -dr com.apple.quarantine"));
  assert.ok(script.includes('hdiutil detach "$VOLUME"'));
  assert.ok(script.trimEnd().endsWith('open -n "$APP"'));
  execFileSync("/bin/sh", ["-n", "-c", script]); // parses as POSIX sh
});

// --- the swap script, run for real against a scratch folder -----------------

async function runSwap(opts: { readonly withOld: boolean; readonly newExists: boolean }) {
  const { mkdtemp, mkdir, writeFile, readFile, readdir } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const root = await mkdtemp(join(tmpdir(), "lol-swap-"));
  const bin = join(root, "bin");
  await mkdir(bin);
  // `open`, `xattr` and `hdiutil` are recorded instead of run.
  for (const tool of ["open", "xattr", "hdiutil"]) {
    await writeFile(join(bin, tool), `#!/bin/sh\necho "${tool} $*" >> "${root}/calls"\n`, { mode: 0o755 });
  }
  const app = join(root, "Applications", "AgonCup.app");
  const fresh = join(root, "stage", "app", "AgonCup.app");
  const previous = join(root, "dmg", "AgonCup.app");
  if (opts.withOld) {
    await mkdir(app, { recursive: true });
    await writeFile(join(app, "version"), "old");
  }
  if (opts.newExists) {
    await mkdir(fresh, { recursive: true });
    await writeFile(join(fresh, "version"), "new");
  }
  await mkdir(previous, { recursive: true });
  const script = buildMacSwapScript({
    pid: 999_999, // not running
    appPath: app,
    newAppPath: fresh,
    previousAppPath: previous,
    volume: null,
    logPath: join(root, "swap.log"),
  });
  let code = 0;
  try {
    execFileSync("/bin/sh", ["-c", script], { env: { ...process.env, PATH: `${bin}:${process.env["PATH"] ?? ""}` } });
  } catch (err) {
    code = (err as { status?: number }).status ?? -1;
  }
  const read = async (p: string): Promise<string | null> => readFile(p, "utf8").catch(() => null);
  return {
    code,
    version: await read(join(app, "version")),
    calls: (await read(join(root, "calls"))) ?? "",
    log: (await read(join(root, "swap.log"))) ?? "",
    appsDir: await readdir(join(root, "Applications")).catch(() => [] as string[]),
    app,
    previous,
  };
}

test("swap script (real sh): replaces the old bundle, removes .old, opens the new app", async () => {
  const r = await runSwap({ withOld: true, newExists: true });
  assert.equal(r.code, 0);
  assert.equal(r.version, "new");
  assert.deepEqual(r.appsDir, ["AgonCup.app"]);
  assert.ok(r.calls.includes(`open -n ${r.app}`));
  assert.ok(r.log.includes("swap done"));
});

test("swap script (real sh): installs into a folder that has no copy yet (e.g. from the .dmg)", async () => {
  const r = await runSwap({ withOld: false, newExists: true });
  assert.equal(r.code, 0);
  assert.equal(r.version, "new");
  assert.ok(r.calls.includes(`open -n ${r.app}`));
});

test("swap script (real sh): a failed move restores the old app and reopens it", async () => {
  const withOld = await runSwap({ withOld: true, newExists: false });
  assert.equal(withOld.code, 1);
  assert.equal(withOld.version, "old");
  assert.deepEqual(withOld.appsDir, ["AgonCup.app"]);
  assert.ok(withOld.calls.includes(`open -n ${withOld.app}`));
  assert.ok(withOld.log.includes("swap failed"));

  // Nothing at the target to fall back on: the copy that was running reopens.
  const fromDmg = await runSwap({ withOld: false, newExists: false });
  assert.equal(fromDmg.code, 1);
  assert.ok(fromDmg.calls.includes(`open -n ${fromDmg.previous}`));
});

// --- Windows: electron-updater ---------------------------------------------

function fakeUpdater(feedVersion: string | null, onInstall?: (u: { emit: (e: Error) => void }) => void) {
  const listeners = new Map<string, Array<(arg: never) => void>>();
  const u = {
    autoDownload: true,
    autoInstallOnAppQuit: false,
    disableDifferentialDownload: false,
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
      onInstall?.({ emit: (e) => (listeners.get("error") ?? []).forEach((fn) => fn(e as never)) });
    },
    listenerCount: (event: string) => (listeners.get(event) ?? []).length,
  };
  return u as typeof u & UpdaterLike;
}

function winDeps(u: UpdaterLike, over: { cacheDir?: string | null; rm?: (p: string) => Promise<void>; wait?: (ms: number) => Promise<void> } = {}) {
  return {
    updater: u,
    feedUrl: "https://x/downloads",
    currentVersion: "0.1.0",
    cacheDir: over.cacheDir ?? null,
    rm: over.rm ?? (async () => {}),
    wait: over.wait ?? (async () => {}),
    log: noLog,
  };
}

test("electronUpdaterInstaller: configures the feed, downloads once, installs silently", async () => {
  const u = fakeUpdater("0.2.0");
  const waits: number[] = [];
  // The real app quits inside quitAndInstall; this fake never does, so wait "forever".
  const inst = electronUpdaterInstaller(
    winDeps(u, { wait: (ms) => { waits.push(ms); return new Promise(() => {}); } }),
  );
  assert.equal(u.feed, "https://x/downloads");
  assert.equal(u.autoDownload, false);
  assert.equal(u.autoInstallOnAppQuit, true);
  assert.equal(u.disableDifferentialDownload, true);

  await assert.rejects(inst.install(release()), /not been downloaded/);

  const progress: number[] = [];
  await inst.prepare(release(), (p) => progress.push(p));
  assert.equal(u.checks, 1);
  assert.equal(u.downloads, 1);
  assert.deepEqual(progress, [55]);
  assert.equal(u.listenerCount("download-progress"), 0, "progress listener detached after the download");

  await inst.prepare(release(), (p) => progress.push(p)); // same version: no second download
  assert.equal(u.downloads, 1);
  assert.deepEqual(progress, [55, 100]);

  void inst.install(release());
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(u.installs, [[true, true]]);
  assert.deepEqual(waits, [30_000]);
});

test("electronUpdaterInstaller: an installer that never starts fails install instead of hanging", async () => {
  const timedOut = fakeUpdater("0.2.0");
  const inst = electronUpdaterInstaller(winDeps(timedOut));
  await inst.prepare(release(), () => {});
  await assert.rejects(inst.install(release()), /did not start.*Download manually/);
  assert.equal(timedOut.listenerCount("error"), 1, "only the permanent logger stays attached");

  const errored = fakeUpdater("0.2.0", ({ emit }) => emit(new Error("spawn EPERM")));
  const inst2 = electronUpdaterInstaller(winDeps(errored, { wait: () => new Promise(() => {}) }));
  await inst2.prepare(release(), () => {});
  await assert.rejects(inst2.install(release()), /could not start \(spawn EPERM\)/);
});

test("electronUpdaterInstaller: a feed that does not list the new version yet fails prepare", async () => {
  const u = fakeUpdater("0.1.0");
  const inst = electronUpdaterInstaller(winDeps(u));
  await assert.rejects(inst.prepare(release(), () => {}), /does not list 0\.2\.0 yet/);
  assert.equal(u.downloads, 0);
});

test("electronUpdaterInstaller: cleanup deletes the used installer, never a download of this run", async () => {
  const removed: string[] = [];
  const rm = async (p: string): Promise<void> => {
    removed.push(p);
  };
  const inst = electronUpdaterInstaller(winDeps(fakeUpdater("0.2.0"), { cacheDir: "/lad/@loldesktop-updater", rm }));
  await inst.cleanup();
  assert.equal(removed.length, 1);
  assert.match(removed[0] ?? "", /@loldesktop-updater[\\/]pending$/);

  await inst.prepare(release(), () => {});
  await inst.cleanup();
  assert.equal(removed.length, 1, "a staged installer is kept");

  const unknown = electronUpdaterInstaller(winDeps(fakeUpdater("0.2.0"), { cacheDir: null, rm }));
  await unknown.cleanup();
  assert.equal(removed.length, 1);

  const failing = electronUpdaterInstaller(
    winDeps(fakeUpdater("0.2.0"), { cacheDir: "/x", rm: async () => { throw new Error("EBUSY"); } }),
  );
  await failing.cleanup(); // never throws
});

// --- macOS: swap ------------------------------------------------------------

function fakeMac(over: Partial<MacSwapDeps> = {}) {
  const calls: string[] = [];
  const files = new Map<string, string>();
  const logs: Array<[string, Record<string, unknown> | undefined]> = [];
  const deps: MacSwapDeps = {
    appBundlePath: "/Applications/AgonCup.app",
    originalBundlePath: null,
    applicationsDirs: ["/Applications", "/Users/p/Applications"],
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
    findAppBundle: async (dir) => `${dir}/AgonCup.app`,
    canReplace: async () => true,
    writeFile: async (p, text) => {
      files.set(p, text);
    },
    readText: async (p) => files.get(p) ?? null,
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
    log: (event, detail) => {
      logs.push([event, detail]);
    },
    ...over,
  };
  return { deps, calls, files, logs };
}

test("macSwapInstaller: download, verify, unpack, then swap script + quit", async () => {
  const { deps, calls, files } = fakeMac();
  const inst = macSwapInstaller(deps);
  const progress: number[] = [];
  await inst.prepare(release(), (p) => progress.push(p));
  assert.deepEqual(calls, [
    "rm /tmp/stage",
    "mkdir /tmp/stage",
    "download https://x/downloads/AgonCup-0.2.0-universal.zip -> /tmp/stage/update.zip",
    "mkdir /tmp/stage/app",
    "extract /tmp/stage/update.zip -> /tmp/stage/app",
  ]);
  assert.deepEqual(progress, [50, 100]);

  calls.length = 0;
  await inst.install(release());
  const script = files.get("/tmp/stage/swap.sh");
  assert.ok(script?.includes("PID=77"));
  assert.ok(script?.includes("APP='/Applications/AgonCup.app'"));
  assert.ok(script?.includes("NEW='/tmp/stage/app/AgonCup.app'"));
  assert.ok(script?.includes("VOLUME=''"));
  assert.deepEqual(calls, ["chmod /tmp/stage/swap.sh 755", "spawn /tmp/stage/swap.sh", "quit"]);
});

test("macSwapInstaller: checksum mismatch discards the download", async () => {
  const { deps, calls } = fakeMac({ sha512Base64: async () => "bad==" });
  const inst = macSwapInstaller(deps);
  await assert.rejects(inst.prepare(release(), () => {}), /integrity check/);
  assert.ok(calls.filter((c) => c === "rm /tmp/stage").length === 2, "staging wiped again after the mismatch");
  await assert.rejects(inst.install(release()), /not been downloaded/);
});

test("macSwapInstaller: no package on the feed never downloads", async () => {
  const noPkg = macSwapInstaller(fakeMac().deps);
  await assert.rejects(noPkg.prepare(release({ packageUrl: null }), () => {}), /No update package/);
});

test("macSwapInstaller: a copy started from the .dmg is installed into Applications and the .dmg ejected", async () => {
  const dmg = fakeMac({
    appBundlePath: TRANSLOCATED,
    originalBundlePath: "/Volumes/AgonCup 0.2.3-universal/AgonCup.app",
  });
  const inst = macSwapInstaller(dmg.deps);
  await inst.prepare(release(), () => {});
  await inst.install(release());
  const script = dmg.files.get("/tmp/stage/swap.sh") ?? "";
  assert.ok(script.includes("APP='/Applications/AgonCup.app'"));
  assert.ok(script.includes("PREVIOUS='/Volumes/AgonCup 0.2.3-universal/AgonCup.app'"));
  assert.ok(script.includes("VOLUME='/Volumes/AgonCup 0.2.3-universal'"));
  assert.ok(dmg.calls.includes("quit"));
});

test("chooseMacInstallTarget: in place when possible, else the first Applications folder that takes it", async () => {
  const inPlace = await chooseMacInstallTarget(fakeMac({ appBundlePath: "/Users/p/Desktop/AgonCup.app" }).deps);
  assert.deepEqual(inPlace, { target: "/Users/p/Desktop/AgonCup.app", previous: "/Users/p/Desktop/AgonCup.app", volume: null });

  // Translocated from /Applications itself (never moved by Finder): replace the real copy.
  const fromApps = await chooseMacInstallTarget(
    fakeMac({ appBundlePath: TRANSLOCATED, originalBundlePath: "/Applications/AgonCup.app" }).deps,
  );
  assert.equal(fromApps?.target, "/Applications/AgonCup.app");

  // Translocated, origin unknown: never the read-only mirror.
  const unknown = await chooseMacInstallTarget(fakeMac({ appBundlePath: TRANSLOCATED }).deps);
  assert.deepEqual(unknown, { target: "/Applications/AgonCup.app", previous: TRANSLOCATED, volume: null });

  // A standard account that may not write /Applications gets ~/Applications.
  const noAdmin = await chooseMacInstallTarget(
    fakeMac({
      appBundlePath: "/Volumes/LoL/AgonCup.app",
      canReplace: async (p) => p.startsWith("/Users/p/"),
    }).deps,
  );
  assert.equal(noAdmin?.target, "/Users/p/Applications/AgonCup.app");
  assert.equal(noAdmin?.volume, "/Volumes/LoL");

  const nowhere = fakeMac({ appBundlePath: "/Volumes/LoL/AgonCup.app", canReplace: async () => false });
  assert.equal(await chooseMacInstallTarget(nowhere.deps), null);
  const inst = macSwapInstaller(nowhere.deps);
  await inst.prepare(release(), () => {});
  await assert.rejects(inst.install(release()), /may not write to the Applications folder/);
  assert.ok(!nowhere.calls.some((c) => c.startsWith("spawn")));
  assert.ok(!nowhere.calls.includes("quit"));
});

test("macSwapInstaller: cleanup logs the last swap and clears staging, never under a staged update", async () => {
  const { deps, calls, files, logs } = fakeMac();
  files.set("/tmp/stage/swap.log", "Mon swap start\nMon swap done\n");
  const inst = macSwapInstaller(deps);
  await inst.cleanup();
  assert.deepEqual(calls, ["rm /tmp/stage"]);
  assert.deepEqual(logs.at(-1), ["update_swap_log", { text: "Mon swap start\nMon swap done\n" }]);

  await inst.prepare(release(), () => {});
  calls.length = 0;
  await inst.cleanup();
  assert.deepEqual(calls, [], "the staged bundle is kept for install()");

  const failing = macSwapInstaller(fakeMac({ rm: async () => { throw new Error("EPERM"); } }).deps);
  await failing.cleanup(); // never throws
});
