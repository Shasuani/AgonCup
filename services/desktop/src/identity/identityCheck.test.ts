/**
 * Unit tests for the advisory LCU identity check (node:test; fake adapter, no
 * real LCU). Covers match / mismatch / unknown and the never-throws contract.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { checkLcuIdentity } from "./identityCheck.js";
import type { CurrentSummoner, Puuid } from "@lol/lcu";

function summoner(puuid: string): CurrentSummoner {
  return {
    puuid: puuid as Puuid,
    summonerId: "123" as never,
    displayName: "Tester",
  };
}

function adapterReturning(s: CurrentSummoner) {
  return { async getCurrentSummoner() {
    return s;
  } };
}

function adapterThrowing() {
  return { async getCurrentSummoner(): Promise<CurrentSummoner> {
    throw new Error("LCU not running");
  } };
}

test("match: LCU PUUID equals the verified PUUID", async () => {
  const res = await checkLcuIdentity({
    adapter: adapterReturning(summoner("puuid-A")),
    verifiedPuuid: "puuid-A" as Puuid,
  });
  assert.equal(res.verdict, "match");
  assert.equal(res.lcuPuuid, "puuid-A");
});

test("mismatch: LCU PUUID differs from the verified PUUID (advisory warning)", async () => {
  const res = await checkLcuIdentity({
    adapter: adapterReturning(summoner("puuid-B")),
    verifiedPuuid: "puuid-A" as Puuid,
  });
  assert.equal(res.verdict, "mismatch");
  assert.equal(res.lcuPuuid, "puuid-B");
  assert.equal(res.verifiedPuuid, "puuid-A");
});

test("unknown: no verified PUUID yet (Account-V1 link pending)", async () => {
  const res = await checkLcuIdentity({
    adapter: adapterReturning(summoner("puuid-B")),
    verifiedPuuid: undefined,
  });
  assert.equal(res.verdict, "unknown");
});

test("unknown: LCU unreachable -> degrades, never throws", async () => {
  const res = await checkLcuIdentity({
    adapter: adapterThrowing(),
    verifiedPuuid: "puuid-A" as Puuid,
  });
  assert.equal(res.verdict, "unknown");
  assert.match(res.detail, /LCU current-summoner unavailable/);
});
