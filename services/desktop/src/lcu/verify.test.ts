/**
 * Unit tests for the desktop verify flow (node:test; stubbed LCU read + fetch,
 * no real LCU / network). Covers success, League-not-running short-circuit, and
 * backend error mapping (404/409 detail passthrough).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { verifyDesktop } from "./verify.js";
import type { ReadSummonerResult } from "./readSummoner.js";

const okSummoner: ReadSummonerResult = {
  ok: true,
  puuid: "lcu-uuid-1",
  summonerId: "42",
  displayName: "",
  gameName: "Chama",
  tagLine: "Shasu",
  region: "eun1",
};

function fakeFetch(status: number, body: unknown): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
}

test("ok: reads LCU Riot ID + region and POSTs the verify request (no identity puuid)", async () => {
  let captured: { url: string; body: string } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, body: String(init?.body) };
    return new Response(
      JSON.stringify({
        account_id: "acc-1",
        puuid: "canonical-78char-puuid",
        tier: "GOLD",
        division: "II",
        league_points: 45,
        rank_text: "Gold II, 45 LP",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await verifyDesktop(
    { code: "ABCD-EFGH" },
    { readSummoner: async () => okSummoner, fetch: spyFetch, backendUrl: "http://x" },
  );

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.accountId, "acc-1");
    // The CANONICAL puuid comes from the backend, not the LCU read.
    assert.equal(result.puuid, "canonical-78char-puuid");
    assert.equal(result.tier, "GOLD");
    assert.equal(result.division, "II");
    assert.equal(result.leaguePoints, 45);
    assert.equal(result.rankText, "Gold II, 45 LP");
  }
  assert.equal(captured?.url, "http://x/api/v1/accounts/verify-desktop");
  const sent = JSON.parse(captured!.body) as Record<string, unknown>;
  assert.equal(sent["code"], "ABCD-EFGH");
  assert.equal(sent["game_name"], "Chama");
  assert.equal(sent["tag_line"], "Shasu");
  assert.equal(sent["region"], "eun1");
  // The LCU puuid is sent as an advisory secondary signal, not the identity.
  assert.equal(sent["lcu_puuid"], "lcu-uuid-1");
  assert.equal(sent["summoner_id"], "42");
  // The canonical identity puuid is NOT sent by the desktop anymore.
  assert.equal("puuid" in sent, false);
});

test("guard: empty Riot ID (gameName/tagLine) short-circuits before any fetch", async () => {
  let fetched = false;
  const spyFetch: typeof fetch = (async () => {
    fetched = true;
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;

  const result = await verifyDesktop(
    { code: "ABCD-EFGH" },
    {
      readSummoner: async () => ({ ...okSummoner, gameName: "", tagLine: "" }),
      fetch: spyFetch,
    },
  );

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
    assert.match(result.detail, /Riot ID/);
  }
  assert.equal(fetched, false);
});

test("league_not_running: short-circuits before any fetch (status 0)", async () => {
  let fetched = false;
  const spyFetch: typeof fetch = (async () => {
    fetched = true;
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;

  const result = await verifyDesktop(
    { code: "ABCD-EFGH" },
    {
      readSummoner: async () => ({
        ok: false,
        reason: "league_not_running",
        detail: "League client is not running or not signed in.",
      }),
      fetch: spyFetch,
    },
  );

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
  }
  assert.equal(fetched, false);
});

test("404: backend detail is passed through to the renderer", async () => {
  const result = await verifyDesktop(
    { code: "BAD" },
    { readSummoner: async () => okSummoner, fetch: fakeFetch(404, { detail: "invalid or expired code" }) },
  );
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 404);
    assert.equal(result.detail, "invalid or expired code");
  }
});

test("409: already-linked detail is passed through", async () => {
  const result = await verifyDesktop(
    { code: "ABCD-EFGH" },
    {
      readSummoner: async () => okSummoner,
      fetch: fakeFetch(409, { detail: "this Riot account is already linked to another user" }),
    },
  );
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 409);
    assert.match(result.detail, /already linked/);
  }
});

test("backend unreachable: maps to status 0", async () => {
  const throwingFetch: typeof fetch = (async () => {
    throw new Error("network down");
  }) as unknown as typeof fetch;

  const result = await verifyDesktop(
    { code: "ABCD-EFGH" },
    { readSummoner: async () => okSummoner, fetch: throwingFetch },
  );
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
  }
});
