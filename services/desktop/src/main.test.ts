/**
 * Unit tests for the Electron main wiring (node:test; no real Electron/WS/LCU).
 *
 * The load-bearing property: importing `main.ts` performs NO IO — no socket, no
 * Electron window — so it is safe under `node --test`. Also exercises the
 * DesktopRuntime lifecycle (match-assigned -> controller created, match-ended ->
 * torn down) and the advisory identity check, all with injected fakes.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { DesktopRuntime, createLcuAdapter } from "./main.js";
import type { WebSocketLike } from "./transport/wsTransport.js";
import { WS_OPEN } from "./transport/wsTransport.js";
import type { Clock, Scheduler } from "./orchestration/index.js";
import type { CurrentSummoner, LcuAdapter, Puuid } from "@lol/lcu";

// Importing main.ts above must not have opened anything; reaching here is the
// import-safety assertion (the test process has no electron/ws sockets live).
test("importing main.ts is IO-free (no Electron window, no socket)", () => {
  // createLcuAdapter is a pure constructor — calling it opens nothing.
  const adapter = createLcuAdapter();
  assert.equal(typeof adapter.getCurrentSummoner, "function");
});

/** A no-op fake socket so DesktopRuntime.onMatchAssigned never touches `ws`. */
class FakeSocket implements WebSocketLike {
  readyState = WS_OPEN;
  send(): void {}
  close(): void {}
  on(): void {}
}

function fakeAdapter(over: Partial<{ summoner: CurrentSummoner; throws: boolean }> = {}): LcuAdapter {
  return {
    async getCurrentSummoner() {
      if (over.throws) throw new Error("no LCU");
      return (
        over.summoner ??
        ({
          puuid: "puuid-A",
          summonerId: "1",
          displayName: "x",
          gameName: "Chama",
          tagLine: "Shasu",
        } as CurrentSummoner)
      );
    },
  } as unknown as LcuAdapter;
}

const SCHEDULER: Scheduler = { every: () => () => {} };
const CLOCK: Clock = { nowMs: () => 0 };

function makeRuntime(over: { verifiedPuuid?: Puuid; adapter?: LcuAdapter; onWarn?: (e: string, d: Record<string, unknown>) => void } = {}) {
  return new DesktopRuntime({
    adapter: over.adapter ?? fakeAdapter(),
    config: {
      realtimeUrl: "wss://host/api/v1/realtime/ws",
      token: "jwt",
      verifiedPuuid: over.verifiedPuuid,
    },
    wsFactory: () => new FakeSocket(),
    scheduler: SCHEDULER,
    clock: CLOCK,
    ...(over.onWarn ? { onWarn: over.onWarn } : {}),
  });
}

test("constructing DesktopRuntime opens nothing; no live matches", () => {
  const rt = makeRuntime();
  assert.equal(rt.liveMatchCount, 0);
});

test("onMatchAssigned creates a controller; onMatchEnded tears it down (idempotent)", () => {
  const rt = makeRuntime();
  rt.onMatchAssigned("m1");
  rt.onMatchAssigned("m1"); // idempotent
  assert.equal(rt.liveMatchCount, 1);
  rt.onMatchAssigned("m2");
  assert.equal(rt.liveMatchCount, 2);
  rt.onMatchEnded("m1");
  assert.equal(rt.liveMatchCount, 1);
  rt.onMatchEnded("missing"); // no-op
  rt.shutdown();
  assert.equal(rt.liveMatchCount, 0);
});

test("validateIdentity: match verdict, no warning", async () => {
  const warnings: string[] = [];
  const rt = makeRuntime({
    verifiedPuuid: "puuid-A" as Puuid,
    onWarn: (e) => warnings.push(e),
  });
  const res = await rt.validateIdentity();
  assert.equal(res.verdict, "match");
  assert.equal(warnings.includes("identity_mismatch"), false);
});

test("validateIdentity: mismatch surfaces an advisory warning, never throws", async () => {
  const warnings: string[] = [];
  const rt = makeRuntime({
    verifiedPuuid: "puuid-Z" as Puuid,
    adapter: fakeAdapter({
      summoner: {
        puuid: "puuid-A",
        summonerId: "1",
        displayName: "x",
        gameName: "Chama",
        tagLine: "Shasu",
      } as CurrentSummoner,
    }),
    onWarn: (e) => warnings.push(e),
  });
  const res = await rt.validateIdentity();
  assert.equal(res.verdict, "mismatch");
  assert.ok(warnings.includes("identity_mismatch"));
});
