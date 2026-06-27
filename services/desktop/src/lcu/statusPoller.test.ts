/**
 * Unit tests for the LCU reachability poller (node:test; injected probes + fake
 * timers, no real LCU). Covers the three-state classification, change-only
 * emission (dedupe), the start()/stop() interval loop, and probe-error fail-safe.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { LcuStatusPoller, type LcuStatus, type PollerTimers } from "./statusPoller.js";

/** Mutable probe pair so a test can flip the LCU state between ticks. */
function probes(initial: { summoner: boolean; lockfile: boolean }) {
  const state = { ...initial };
  return {
    state,
    probeSummoner: async () => state.summoner,
    probeLockfile: async () => state.lockfile,
  };
}

test("online: summoner reads ok", async () => {
  const p = probes({ summoner: true, lockfile: true });
  const seen: LcuStatus[] = [];
  const poller = new LcuStatusPoller({ ...p, onStatus: (s) => seen.push(s) });
  assert.equal(await poller.tick(), "online");
  assert.deepEqual(seen, ["online"]);
});

test("loading: lockfile present but no summoner", async () => {
  const p = probes({ summoner: false, lockfile: true });
  const poller = new LcuStatusPoller({ ...p, onStatus: () => {} });
  assert.equal(await poller.tick(), "loading");
});

test("offline: no lockfile, no summoner", async () => {
  const p = probes({ summoner: false, lockfile: false });
  const poller = new LcuStatusPoller({ ...p, onStatus: () => {} });
  assert.equal(await poller.tick(), "offline");
});

test("transitions: offline -> loading -> online emits once per change (deduped)", async () => {
  const p = probes({ summoner: false, lockfile: false });
  const seen: LcuStatus[] = [];
  const poller = new LcuStatusPoller({ ...p, onStatus: (s) => seen.push(s) });

  await poller.tick(); // offline
  await poller.tick(); // offline again -> no emit
  p.state.lockfile = true;
  await poller.tick(); // loading
  await poller.tick(); // loading again -> no emit
  p.state.summoner = true;
  await poller.tick(); // online
  p.state.summoner = false;
  p.state.lockfile = false;
  await poller.tick(); // offline

  assert.deepEqual(seen, ["offline", "loading", "online", "offline"]);
});

test("probe error is fail-safe (treated as offline, never throws)", async () => {
  const poller = new LcuStatusPoller({
    probeSummoner: async () => {
      throw new Error("boom");
    },
    probeLockfile: async () => true,
    onStatus: () => {},
  });
  assert.equal(await poller.tick(), "offline");
});

test("start() ticks immediately then on the interval; stop() halts", async () => {
  let scheduled: (() => void) | null = null;
  const timers: PollerTimers = {
    setInterval(fn) {
      scheduled = fn;
      return { id: 1 };
    },
    clearInterval() {
      scheduled = null;
    },
  };
  const p = probes({ summoner: true, lockfile: true });
  const seen: LcuStatus[] = [];
  const poller = new LcuStatusPoller({ ...p, onStatus: (s) => seen.push(s), timers });

  poller.start();
  // Let the immediate tick's async probe settle.
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(seen, ["online"]);
  assert.notEqual(scheduled, null);

  p.state.summoner = false; // -> loading on the next scheduled tick
  scheduled!();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(seen, ["online", "loading"]);

  poller.stop();
  assert.equal(scheduled, null);
});
