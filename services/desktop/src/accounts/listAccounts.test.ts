/**
 * Unit tests for the authenticated "my linked accounts" fetch (node:test; stubbed
 * fetch, no real network). Covers success mapping (snake_case -> camelCase), the
 * Bearer header, empty list, and error / unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { listMyAccounts } from "./listAccounts.js";

test("ok: GETs /accounts/me with Bearer and maps the rows", async () => {
  let captured:
    | { url: string; headers: Record<string, string>; method: string | undefined }
    | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = {
      url,
      method: init?.method,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    return new Response(
      JSON.stringify([
        {
          account_id: "a1",
          game_name: "Chama",
          tag_line: "Shasu",
          region: "eun1",
          verified: true,
          rank_text: "Gold II, 45 LP",
        },
        { account_id: "a2", game_name: "Smurf", tag_line: "EUNE", verified: false, rank_text: "Unranked" },
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await listMyAccounts(
    { accessToken: "jwt-abc" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );

  assert.equal(captured?.url, "http://x/api/v1/accounts/me");
  assert.equal(captured?.method, "GET");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt-abc");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.accounts.length, 2);
    const [a, b] = result.accounts;
    assert.ok(a && b);
    assert.equal(a.accountId, "a1");
    assert.equal(a.gameName, "Chama");
    assert.equal(a.tagLine, "Shasu");
    assert.equal(a.rankText, "Gold II, 45 LP");
    assert.equal(b.verified, false);
    assert.equal(b.region, null);
  }
});

test("ok: empty list returns no accounts", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
  const result = await listMyAccounts({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.accounts.length, 0);
});

test("401 is surfaced so the caller can refresh + retry", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await listMyAccounts({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
});

test("network failure maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await listMyAccounts({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});
