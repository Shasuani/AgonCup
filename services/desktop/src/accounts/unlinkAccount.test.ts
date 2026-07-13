/**
 * Unit tests for the account-unlink call (node:test; stubbed fetch, no real
 * network). Covers success mapping (snake_case -> camelCase), the Bearer header,
 * DELETE method + URL, error detail, 401 surfacing, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { unlinkAccount } from "./unlinkAccount.js";

test("unlinkAccount: DELETEs /accounts/me/{id} with Bearer and maps the row", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(
      JSON.stringify({
        account_id: "a1",
        game_name: "Chama",
        tag_line: "Shasu",
        region: "eun1",
        verified: true,
        rank_text: "Gold II, 45 LP",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await unlinkAccount({ accessToken: "jwt", accountId: "a1" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/accounts/me/a1");
  assert.equal(captured?.method, "DELETE");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.account.accountId, "a1");
    assert.equal(result.account.gameName, "Chama");
    assert.equal(result.account.rankText, "Gold II, 45 LP");
  }
});

test("unlinkAccount: encodes the account id in the URL", async () => {
  let captured: { url: string } | undefined;
  const spyFetch: typeof fetch = (async (url: string) => {
    captured = { url };
    return new Response(JSON.stringify({ account_id: "a b" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
  await unlinkAccount({ accessToken: "j", accountId: "a b" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(captured?.url, "http://x/api/v1/accounts/me/a%20b");
});

test("unlinkAccount: error surfaces detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await unlinkAccount({ accessToken: "j", accountId: "x" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 404);
    assert.equal(result.detail, "not found");
  }
});

test("401 is surfaced so the caller can refresh + retry", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await unlinkAccount({ accessToken: "j", accountId: "x" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
});

test("network failure maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await unlinkAccount({ accessToken: "j", accountId: "x" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});
