/**
 * Unit tests for the champ-select board poller (node:test; injected reads, no
 * real LCU). Covers the null-outside-select case, view building (names resolved
 * via the lazy catalogue, ids-only fallback while it is unavailable), the
 * change-only emission dedupe, and the read-failure fail-safe.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChampSelectSnapshot } from "@lol/lcu";
import { ChampSelectPoller, type ChampSelectView } from "./champSelectPoller.js";

const SCHEMA = "test" as ChampSelectSnapshot["schemaVersion"];

function snapshot(overrides: Partial<ChampSelectSnapshot> = {}): ChampSelectSnapshot {
  return {
    source: "champSelectSession",
    timerPhase: "BAN_PICK",
    members: [
      { cellId: 0, side: "my", gameName: "Alice", championId: 103, locked: true },
      { cellId: 5, side: "their", championId: 0, locked: false },
    ],
    schemaVersion: SCHEMA,
    ...overrides,
  };
}

const NAMES = new Map<number, string>([[103, "Ahri"]]);

test("outside champ select: emits null once, stays silent after", async () => {
  const seen: Array<ChampSelectView | null> = [];
  const poller = new ChampSelectPoller({
    readSession: async () => null,
    readChampionNames: async () => NAMES,
    onUpdate: (v) => seen.push(v),
  });
  await poller.tick();
  await poller.tick();
  assert.deepEqual(seen, [null]);
});

test("in champ select: builds the view with resolved champion names", async () => {
  const seen: Array<ChampSelectView | null> = [];
  const poller = new ChampSelectPoller({
    readSession: async () => snapshot(),
    readChampionNames: async () => NAMES,
    onUpdate: (v) => seen.push(v),
  });
  const view = await poller.tick();
  assert.deepEqual(view, {
    timerPhase: "BAN_PICK",
    members: [
      { cellId: 0, side: "my", gameName: "Alice", championId: 103, championName: "Ahri", locked: true },
      { cellId: 5, side: "their", championId: 0, locked: false },
    ],
  });
  assert.equal(seen.length, 1);
});

test("catalogue failure degrades to ids-only, then heals on the next board", async () => {
  let namesFail = true;
  const seen: Array<ChampSelectView | null> = [];
  const poller = new ChampSelectPoller({
    readSession: async () => snapshot(),
    readChampionNames: async () => {
      if (namesFail) throw new Error("client busy");
      return NAMES;
    },
    onUpdate: (v) => seen.push(v),
  });

  const first = await poller.tick();
  assert.equal(first?.members[0]?.championName, undefined); // ids-only
  namesFail = false;
  const second = await poller.tick();
  assert.equal(second?.members[0]?.championName, "Ahri"); // healed
  assert.equal(seen.length, 2); // the healed view is a content change -> re-emit
});

test("unchanged board is deduped; a lock flip re-emits", async () => {
  let locked = false;
  const seen: Array<ChampSelectView | null> = [];
  const poller = new ChampSelectPoller({
    readSession: async () =>
      snapshot({
        members: [{ cellId: 0, side: "my", championId: 103, locked }],
      }),
    readChampionNames: async () => NAMES,
    onUpdate: (v) => seen.push(v),
  });

  await poller.tick();
  await poller.tick(); // identical -> no emit
  locked = true;
  await poller.tick(); // lock flip -> emit
  assert.equal(seen.length, 2);
  assert.equal(seen[1]?.members[0]?.locked, true);
});

test("session read failure is fail-safe null, and leaving select emits null", async () => {
  let phase: "in" | "throw" | "out" = "in";
  const seen: Array<ChampSelectView | null> = [];
  const poller = new ChampSelectPoller({
    readSession: async () => {
      if (phase === "throw") throw new Error("lcu gone");
      return phase === "in" ? snapshot() : null;
    },
    readChampionNames: async () => NAMES,
    onUpdate: (v) => seen.push(v),
  });

  await poller.tick(); // board
  phase = "throw";
  await poller.tick(); // failure -> null (change) -> emit
  phase = "out";
  await poller.tick(); // still null -> deduped
  assert.equal(seen.length, 2);
  assert.equal(seen[1], null);
});

test("start()/stop() drive the injected interval timer", async () => {
  let intervalFn: (() => void) | null = null;
  let cleared = false;
  const poller = new ChampSelectPoller({
    readSession: async () => null,
    readChampionNames: async () => NAMES,
    onUpdate: () => {},
    timers: {
      setInterval: (fn) => {
        intervalFn = fn;
        return "handle";
      },
      clearInterval: () => {
        cleared = true;
      },
    },
  });
  poller.start();
  assert.notEqual(intervalFn, null); // interval armed
  poller.stop();
  assert.equal(cleared, true);
});
