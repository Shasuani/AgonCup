/**
 * Unit tests for the auto-update wiring (node:test; fake updater + manual
 * scheduler, no Electron / network). Pins the per-platform contract: Windows
 * downloads and installs on consent, macOS only points at the .dmg, prompts
 * fire once per version, and updater errors never escape.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { Scheduler } from "./orchestration/index.js";
import {
  MAC_INSTALLER_FILE,
  UPDATE_CHECK_INTERVAL_MS,
  UPDATE_FIRST_CHECK_DELAY_MS,
  wireAutoUpdate,
  type AutoUpdateDeps,
  type UpdateInfoLike,
  type UpdaterLike,
} from "./updater.js";

type Listener = (arg: never) => void;

/** In-memory electron-updater stand-in that records what was done to it. */
function fakeUpdater(): UpdaterLike & {
  emit(event: "update-available" | "update-downloaded", info: UpdateInfoLike): void;
  emitError(error: Error): void;
  feed: string | null;
  checks: number;
  installs: Array<[boolean | undefined, boolean | undefined]>;
  checkImpl: () => Promise<unknown>;
} {
  const listeners = new Map<string, Listener[]>();
  const add = (event: string, fn: Listener): void => {
    listeners.set(event, [...(listeners.get(event) ?? []), fn]);
  };
  const fire = (event: string, arg: unknown): void => {
    for (const fn of listeners.get(event) ?? []) fn(arg as never);
  };
  return {
    autoDownload: true,
    autoInstallOnAppQuit: false,
    feed: null,
    checks: 0,
    installs: [],
    checkImpl: async () => null,
    setFeedURL(options) {
      this.feed = options.url;
    },
    on(event: string, listener: Listener) {
      add(event, listener);
      return this;
    },
    async checkForUpdates() {
      this.checks += 1;
      return this.checkImpl();
    },
    quitAndInstall(isSilent, isForceRunAfter) {
      this.installs.push([isSilent, isForceRunAfter]);
    },
    emit(event, info) {
      fire(event, info);
    },
    emitError(error) {
      fire("error", error);
    },
  };
}

/** Manual scheduler: remembers every interval, fires them on demand. */
function manualScheduler(): Scheduler & {
  timers: Array<{ ms: number; fn: () => void; cancelled: boolean }>;
  fire(ms: number): void;
} {
  const timers: Array<{ ms: number; fn: () => void; cancelled: boolean }> = [];
  return {
    timers,
    every(ms, fn) {
      const t = { ms, fn, cancelled: false };
      timers.push(t);
      return () => {
        t.cancelled = true;
      };
    },
    fire(ms) {
      for (const t of timers) if (t.ms === ms && !t.cancelled) t.fn();
    },
  };
}

interface Harness {
  updater: ReturnType<typeof fakeUpdater>;
  scheduler: ReturnType<typeof manualScheduler>;
  opened: string[];
  restartAnswers: boolean[];
  downloadAnswers: boolean[];
  askedRestart: string[];
  askedDownload: string[];
  events: string[];
  deps: AutoUpdateDeps;
}

function harness(platform: string): Harness {
  const updater = fakeUpdater();
  const scheduler = manualScheduler();
  const h: Harness = {
    updater,
    scheduler,
    opened: [],
    restartAnswers: [],
    downloadAnswers: [],
    askedRestart: [],
    askedDownload: [],
    events: [],
    deps: {
      updater,
      platform,
      downloadsUrl: "https://lol.gameras.gr/downloads",
      scheduler,
      askRestart: async (v) => {
        h.askedRestart.push(v);
        return h.restartAnswers.shift() ?? false;
      },
      askDownload: async (v) => {
        h.askedDownload.push(v);
        return h.downloadAnswers.shift() ?? false;
      },
      openExternal: (url) => {
        h.opened.push(url);
      },
      log: (event) => {
        h.events.push(event);
      },
    },
  };
  return h;
}

/** Let the prompt promises settle. */
const tick = (): Promise<void> => new Promise((r) => setImmediate(r));

test("windows: feed set, background download on, restart consent installs silently", async () => {
  const h = harness("win32");
  wireAutoUpdate(h.deps);

  assert.equal(h.updater.feed, "https://lol.gameras.gr/downloads");
  assert.equal(h.updater.autoDownload, true);
  assert.equal(h.updater.autoInstallOnAppQuit, true);

  // A found update is just logged on Windows — the download is automatic.
  h.updater.emit("update-available", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedDownload, []);
  assert.deepEqual(h.opened, []);

  h.restartAnswers.push(true);
  h.updater.emit("update-downloaded", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedRestart, ["0.2.0"]);
  assert.deepEqual(h.updater.installs, [[true, true]]);
});

test("windows: 'later' defers to install-on-quit and is not re-asked for the same version", async () => {
  const h = harness("win32");
  wireAutoUpdate(h.deps);

  h.restartAnswers.push(false);
  h.updater.emit("update-downloaded", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.updater.installs, []);
  assert.ok(h.events.includes("update_deferred"));

  h.updater.emit("update-downloaded", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedRestart, ["0.2.0"]);

  // A NEWER version is a fresh question.
  h.restartAnswers.push(true);
  h.updater.emit("update-downloaded", { version: "0.3.0" });
  await tick();
  assert.deepEqual(h.askedRestart, ["0.2.0", "0.3.0"]);
  assert.equal(h.updater.installs.length, 1);
});

test("mac: never downloads; consent opens the .dmg on the site, once per version", async () => {
  const h = harness("darwin");
  wireAutoUpdate(h.deps);

  assert.equal(h.updater.autoDownload, false);
  assert.equal(h.updater.autoInstallOnAppQuit, false);

  h.downloadAnswers.push(true);
  h.updater.emit("update-available", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedDownload, ["0.2.0"]);
  assert.deepEqual(h.opened, [`https://lol.gameras.gr/downloads/${MAC_INSTALLER_FILE}`]);

  // The 4-hourly re-check re-emits update-available; the player is not nagged.
  h.updater.emit("update-available", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedDownload, ["0.2.0"]);

  // And a downloaded event can never install anything on mac.
  h.updater.emit("update-downloaded", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.askedRestart, []);
  assert.deepEqual(h.updater.installs, []);
});

test("mac: declining leaves the browser alone", async () => {
  const h = harness("darwin");
  wireAutoUpdate(h.deps);
  h.downloadAnswers.push(false);
  h.updater.emit("update-available", { version: "0.2.0" });
  await tick();
  assert.deepEqual(h.opened, []);
});

test("start: one delayed first check, then periodic; stop cancels both", async () => {
  const h = harness("win32");
  const handle = wireAutoUpdate(h.deps);
  handle.start();
  handle.start(); // idempotent

  assert.deepEqual(
    h.scheduler.timers.map((t) => t.ms),
    [UPDATE_FIRST_CHECK_DELAY_MS, UPDATE_CHECK_INTERVAL_MS],
  );
  assert.equal(h.updater.checks, 0);

  h.scheduler.fire(UPDATE_FIRST_CHECK_DELAY_MS);
  await tick();
  assert.equal(h.updater.checks, 1);
  // The first-check timer is a one-shot: it cancelled itself.
  assert.equal(h.scheduler.timers[0]?.cancelled, true);

  h.scheduler.fire(UPDATE_CHECK_INTERVAL_MS);
  await tick();
  assert.equal(h.updater.checks, 2);

  handle.stop();
  assert.ok(h.scheduler.timers.every((t) => t.cancelled));
});

test("a failing check and an updater error are logged, never thrown", async () => {
  const h = harness("win32");
  const handle = wireAutoUpdate(h.deps);
  h.updater.checkImpl = async () => {
    throw new Error("feed unreachable");
  };
  await handle.checkNow();
  assert.ok(h.events.includes("update_check_failed"));

  h.updater.emitError(new Error("boom"));
  assert.ok(h.events.includes("update_error"));
});

test("an unsupported platform touches nothing", async () => {
  const h = harness("linux");
  const handle = wireAutoUpdate(h.deps);
  handle.start();
  await handle.checkNow();
  assert.equal(h.updater.feed, null);
  assert.equal(h.updater.checks, 0);
  assert.deepEqual(h.scheduler.timers, []);
  assert.ok(h.events.includes("update_unsupported_platform"));
});
