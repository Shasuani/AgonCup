/**
 * Unit tests for the link-before-lock-in step (node:test; stubbed fetch, no real
 * network, no LCU). Covers: an already linked account sends nothing (no DM
 * spam), an unlinked one is linked through `verify-lcu` with the summoner
 * lock-in already read, an unverified row counts as not linked, and every
 * failure (list, verify, 401) comes back typed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ensureActiveAccountLinked } from "./ensureLinked.js";
import type { CurrentSummonerOk } from "../lcu/readSummoner.js";

const ACC2: CurrentSummonerOk = {
  ok: true,
  puuid: "lcu-uuid-acc2",
  summonerId: "222",
  displayName: "",
  gameName: "Acc Two",
  tagLine: "EUNE",
  region: "eun1",
};

interface Call {
  readonly url: string;
  readonly method: string | undefined;
  readonly body: unknown;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(routes: Record<string, () => Response>) {
  const calls: Call[] = [];
  const doFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: init?.method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    const route = Object.keys(routes).find((suffix) => url.endsWith(suffix));
    assert.ok(route, `unexpected request: ${url}`);
    return routes[route]!();
  }) as unknown as typeof fetch;
  return { calls, doFetch };
}

const linkedRow = (gameName: string, tagLine: string, verified = true) => ({
  account_id: "a1",
  game_name: gameName,
  tag_line: tagLine,
  region: "eun1",
  verified,
  rank_text: "Unranked",
});

test("already linked: nothing is sent to verify-lcu (no DM on every lock-in)", async () => {
  const { calls, doFetch } = stubFetch({
    // Riot IDs are compared case- and whitespace-insensitively.
    "/accounts/me": () => json(200, [linkedRow("Acc One", "EUNE"), linkedRow(" acc two ", "eune")]),
  });
  const result = await ensureActiveAccountLinked(
    { accessToken: "jwt", summoner: ACC2 },
    { fetch: doFetch, backendUrl: "http://x" },
  );
  assert.deepEqual(result, { ok: true, outcome: "already-linked", riotId: "Acc Two#EUNE" });
  assert.deepEqual(
    calls.map((c) => c.url),
    ["http://x/api/v1/accounts/me"],
  );
});

test("not linked: the OPEN account is linked via verify-lcu, from the read lock-in made", async () => {
  const { calls, doFetch } = stubFetch({
    "/accounts/me": () => json(200, [linkedRow("Acc One", "EUNE")]),
    "/accounts/verify-lcu": () =>
      json(200, { account_id: "a2", puuid: "canonical", game_name: "Acc Two", tag_line: "EUNE" }),
  });
  const result = await ensureActiveAccountLinked(
    { accessToken: "jwt", summoner: ACC2 },
    { fetch: doFetch, backendUrl: "http://x" },
  );
  assert.deepEqual(result, { ok: true, outcome: "linked", riotId: "Acc Two#EUNE" });
  assert.equal(calls.length, 2);
  assert.equal(calls[1]?.url, "http://x/api/v1/accounts/verify-lcu");
  assert.equal(calls[1]?.method, "POST");
  assert.deepEqual(calls[1]?.body, {
    game_name: "Acc Two",
    tag_line: "EUNE",
    region: "eun1",
    lcu_puuid: "lcu-uuid-acc2",
    summoner_id: "222",
  });
});

test("an unverified row does not count as linked (the region gate wants verified)", async () => {
  const { calls, doFetch } = stubFetch({
    "/accounts/me": () => json(200, [linkedRow("Acc Two", "EUNE", false)]),
    "/accounts/verify-lcu": () => json(200, { account_id: "a2", puuid: "canonical" }),
  });
  const result = await ensureActiveAccountLinked(
    { accessToken: "jwt", summoner: ACC2 },
    { fetch: doFetch, backendUrl: "http://x" },
  );
  assert.equal(result.ok && result.outcome, "linked");
  assert.equal(calls.length, 2);
});

test("verify refused (account belongs to someone else): typed failure with the detail", async () => {
  const { doFetch } = stubFetch({
    "/accounts/me": () => json(200, []),
    "/accounts/verify-lcu": () =>
      json(409, { detail: "this Riot account is already linked to another user" }),
  });
  const result = await ensureActiveAccountLinked(
    { accessToken: "jwt", summoner: ACC2 },
    { fetch: doFetch, backendUrl: "http://x" },
  );
  assert.deepEqual(result, {
    ok: false,
    status: 409,
    detail: "this Riot account is already linked to another user",
  });
});

test("list fails: nothing is linked blind, and a 401 is surfaced for the refresh+retry", async () => {
  const { calls, doFetch } = stubFetch({
    "/accounts/me": () => json(401, { detail: "token expired" }),
  });
  const result = await ensureActiveAccountLinked(
    { accessToken: "jwt", summoner: ACC2 },
    { fetch: doFetch, backendUrl: "http://x" },
  );
  assert.deepEqual(result, { ok: false, status: 401, detail: "token expired" });
  assert.equal(calls.length, 1);
});
