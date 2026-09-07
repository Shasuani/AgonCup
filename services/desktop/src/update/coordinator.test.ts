/**
 * The update state machine (node:test; manual scheduler, fake check + installer).
 * Pins: silent boot + 30-minute cadence, one notification per version,
 * background prepare → ready, manual-only error surfacing, install/retry paths,
 * and that a check never interrupts a download.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { Scheduler } from "../orchestration/index.js";
import type { ReleaseInfo, VersionCheckResult } from "./check.js";
import {
  UPDATE_CHECK_INTERVAL_MS,
  UPDATE_FIRST_CHECK_DELAY_MS,
  createUpdates,
  type UpdateState,
  type UpdatesDeps,
} from "./coordinator.js";
import type { Installer } from "./installers.js";

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

function fakeScheduler() {
  const timers: Array<{ ms: number; fn: () => void; cancelled: boolean }> = [];
  const scheduler: Scheduler = {
    every(ms, fn) {
      const t = { ms, fn, cancelled: false };
      timers.push(t);
      return () => {
        t.cancelled = true;
      };
    },
  };
  return {
    scheduler,
    timers,
    tick(ms: number) {
      for (const t of [...timers]) if (!t.cancelled && t.ms === ms) t.fn();
    },
  };
}

const rel = (over: Partial<ReleaseInfo> = {}): ReleaseInfo => ({
  platform: "win32",
  current: "0.1.0",
  latest: "0.2.0",
  minimum: null,
  status: "update_available",
  upToDate: false,
  required: false,
  publishedAt: null,
  downloadUrl: "https://x/downloads/LeagueOfLeagues-Setup.exe",
  packageUrl: "https://x/downloads/LeagueOfLeagues-Setup-0.2.0.exe",
  packageSha512: null,
  packageSize: null,
  feedUrl: "https://x/downloads",
  ...over,
});

function fakeInstaller() {
  let resolvePrepare: (() => void) | null = null;
  let rejectPrepare: ((e: Error) => void) | null = null;
  const inst = {
    prepares: 0,
    installs: 0,
    failInstall: null as Error | null,
    lastProgress: null as ((p: number) => void) | null,
    kind: "electron-updater" as const,
    prepare(_r: ReleaseInfo, onProgress: (p: number) => void) {
      inst.prepares += 1;
      inst.lastProgress = onProgress;
      return new Promise<void>((res, rej) => {
        resolvePrepare = res;
        rejectPrepare = rej;
      });
    },
    async install() {
      inst.installs += 1;
      if (inst.failInstall) throw inst.failInstall;
    },
    finishPrepare: () => resolvePrepare?.(),
    failPrepare: (msg: string) => rejectPrepare?.(new Error(msg)),
  };
  return inst as typeof inst & Installer;
}

function setup(over: Partial<UpdatesDeps> = {}) {
  const sched = fakeScheduler();
  const installer = fakeInstaller();
  const states: UpdateState[] = [];
  const notifications: Array<{ version: string; required: boolean }> = [];
  const checks: string[] = [];
  let answer: VersionCheckResult = { ok: true, release: rel() };
  const deps: UpdatesDeps = {
    currentVersion: "0.1.0",
    platform: "win32",
    check: async () => {
      checks.push("check");
      return answer;
    },
    installer,
    scheduler: sched.scheduler,
    now: () => 1_700_000_000_000,
    onState: (s) => states.push(s),
    notify: (n) => notifications.push(n),
    log: () => {},
    ...over,
  };
  const updates = createUpdates(deps);
  return {
    updates,
    sched,
    installer,
    states,
    notifications,
    checks,
    setAnswer: (a: VersionCheckResult) => {
      answer = a;
    },
  };
}

test("start: one silent check after the grace period, then every 30 minutes", async () => {
  const t = setup();
  t.setAnswer({ ok: true, release: rel({ status: "up_to_date", latest: "0.1.0", upToDate: true }) });
  t.updates.start();
  assert.deepEqual(
    t.sched.timers.map((x) => x.ms),
    [UPDATE_FIRST_CHECK_DELAY_MS, UPDATE_CHECK_INTERVAL_MS],
  );
  assert.equal(UPDATE_CHECK_INTERVAL_MS, 30 * 60 * 1000);
  t.sched.tick(UPDATE_FIRST_CHECK_DELAY_MS);
  await flush();
  assert.equal(t.checks.length, 1);
  assert.equal(t.sched.timers[0]?.cancelled, true, "first check is a one-shot");
  t.sched.tick(UPDATE_CHECK_INTERVAL_MS);
  await flush();
  assert.equal(t.checks.length, 2);
  t.updates.stop();
  assert.ok(t.sched.timers.every((x) => x.cancelled));
});

test("up to date: no notification, phase up_to_date with the latest recorded", async () => {
  const t = setup();
  t.setAnswer({ ok: true, release: rel({ status: "up_to_date", latest: "0.1.0", upToDate: true }) });
  const s = await t.updates.checkNow("startup");
  assert.equal(s.phase, "up_to_date");
  assert.equal(s.latest, "0.1.0");
  assert.equal(s.lastCheckedAt, "2023-11-14T22:13:20.000Z");
  assert.equal(t.notifications.length, 0);
  assert.equal(t.installer.prepares, 0);
});

test("update available: notifies once per version, prepares in the background, ends ready", async () => {
  const t = setup();
  const s = await t.updates.checkNow("startup");
  assert.equal(s.phase, "downloading");
  assert.equal(s.latest, "0.2.0");
  assert.equal(s.downloadUrl, "https://x/downloads/LeagueOfLeagues-Setup.exe");
  assert.deepEqual(t.notifications, [{ version: "0.2.0", required: false }]);
  assert.equal(t.installer.prepares, 1);

  t.installer.lastProgress?.(37.6);
  assert.equal(t.updates.state().progress, 38);

  // A periodic check during the download is a no-op.
  await t.updates.checkNow("periodic");
  assert.equal(t.checks.length, 1);

  t.installer.finishPrepare();
  await flush();
  assert.equal(t.updates.state().phase, "ready");
  assert.equal(t.updates.state().progress, 100);

  // The next silent check sees the same version: stays ready, no second toast/download.
  await t.updates.checkNow("periodic");
  assert.equal(t.updates.state().phase, "ready");
  assert.equal(t.notifications.length, 1);
  assert.equal(t.installer.prepares, 1);

  // Install from ready → installing.
  const after = await t.updates.install();
  assert.equal(after.phase, "installing");
  assert.equal(t.installer.installs, 1);
});

test("required flag and a new version re-notify", async () => {
  const t = setup({ autoPrepare: false });
  await t.updates.checkNow("startup");
  t.setAnswer({ ok: true, release: rel({ latest: "0.3.0", required: true }) });
  const s = await t.updates.checkNow("periodic");
  assert.equal(s.latest, "0.3.0");
  assert.equal(s.required, true);
  assert.deepEqual(t.notifications, [
    { version: "0.2.0", required: false },
    { version: "0.3.0", required: true },
  ]);
});

test("check failures: silent ones stay quiet, manual ones surface the error", async () => {
  const t = setup();
  t.setAnswer({ ok: false, status: 0, detail: "Backend unreachable" });
  const s1 = await t.updates.checkNow("startup");
  assert.equal(s1.phase, "idle");
  assert.equal(s1.error, null);
  const s2 = await t.updates.checkNow("manual");
  assert.equal(s2.phase, "idle");
  assert.match(s2.error ?? "", /Could not check for updates: Backend unreachable/);
  assert.equal(t.notifications.length, 0);
});

test("unknown feed: phase unknown, only a manual check explains it", async () => {
  const t = setup();
  t.setAnswer({ ok: true, release: rel({ status: "unknown", latest: null, upToDate: true }) });
  const s1 = await t.updates.checkNow("periodic");
  assert.equal(s1.phase, "unknown");
  assert.equal(s1.error, null);
  const s2 = await t.updates.checkNow("manual");
  assert.match(s2.error ?? "", /could not read the release feed/);
});

test("prepare failure → failed with the reason; install retries and then installs", async () => {
  const t = setup();
  await t.updates.checkNow("startup");
  t.installer.failPrepare("The update feed does not list 0.2.0 yet");
  await flush();
  assert.equal(t.updates.state().phase, "failed");
  assert.match(t.updates.state().error ?? "", /does not list/);

  const pending = t.updates.install();
  await flush();
  assert.equal(t.updates.state().phase, "downloading");
  assert.equal(t.installer.prepares, 2);
  t.installer.finishPrepare();
  const s = await pending;
  assert.equal(s.phase, "installing");
  assert.equal(t.installer.installs, 1);
});

test("install failure is reported, and a later check does not restart the download unasked", async () => {
  const t = setup();
  await t.updates.checkNow("startup");
  t.installer.finishPrepare();
  await flush();
  t.installer.failInstall = new Error("This copy runs from /Volumes/X, which cannot be replaced");
  const s = await t.updates.install();
  assert.equal(s.phase, "failed");
  assert.match(s.error ?? "", /cannot be replaced/);
  await t.updates.checkNow("periodic");
  assert.equal(t.installer.prepares, 1, "a failed install is not retried by the timer");
  assert.equal(t.updates.state().phase, "available");
  assert.match(t.updates.state().error ?? "", /cannot be replaced/, "the reason stays visible");
});

test("no installer (dev run): install explains and keeps the manual link", async () => {
  const t = setup({ installer: null });
  assert.equal(t.updates.state().supported, false);
  const s = await t.updates.checkNow("startup");
  assert.equal(s.phase, "available");
  const after = await t.updates.install();
  assert.equal(after.phase, "failed");
  assert.match(after.error ?? "", /cannot update itself/);
  assert.equal(after.downloadUrl, "https://x/downloads/LeagueOfLeagues-Setup.exe");
});
