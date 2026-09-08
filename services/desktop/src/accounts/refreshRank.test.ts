/**
 * Unit tests for the live-rank refresh call (node:test; stubbed fetch, no real
 * network). Covers the POST + Bearer + id-in-path shape, the row mapping, the
 * empty-id guard, and error / unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { refreshAccountRank } from "./refreshRank.js";

test("ok: POSTs /accounts/me/{id}/rank with Bearer and maps the row", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(
      JSON.stringify({ account_id: "a1", game_name: "Chama", tag_line: "Shasu", region: "eun1", verified: true, rank_text: "Gold II, 45 LP" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await refreshAccountRank(
    { accessToken: "jwt-abc", accountId: "a1" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );

  assert.equal(captured?.url, "http://x/api/v1/accounts/me/a1/rank");
  assert.equal(captured?.method, "POST");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt-abc");
  assert.deepEqual(result, {
    ok: true,
    account: { accountId: "a1", gameName: "Chama", tagLine: "Shasu", region: "eun1", verified: true, rankText: "Gold II, 45 LP" },
  });
});

test("guard: an empty account id never hits the network", async () => {
  let calls = 0;
  const spyFetch: typeof fetch = (async () => {
    calls += 1;
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
  const result = await refreshAccountRank({ accessToken: "t", accountId: "" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(calls, 0);
  assert.equal(result.ok, false);
});

test("401 / 404: status and detail are passed through", async () => {
  for (const [status, detail] of [[401, "token expired"], [404, "account not linked"]] as const) {
    const spyFetch: typeof fetch = (async () =>
      new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    const result = await refreshAccountRank({ accessToken: "t", accountId: "a1" }, { fetch: spyFetch, backendUrl: "http://x" });
    assert.deepEqual(result, { ok: false, status, detail });
  }
});

test("unreachable backend maps to status 0, never throws", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await refreshAccountRank({ accessToken: "t", accountId: "a1" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
    assert.match(result.detail, /unreachable/i);
  }
});
