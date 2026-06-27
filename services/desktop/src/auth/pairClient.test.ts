/**
 * Unit tests for the desktop pairing + refresh client (node:test; mock fetch, no
 * network). Covers: pair maps tokens + user + account; pair error passthrough;
 * refresh rotates tokens; refresh 401 -> error; network failure -> status 0.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { pairDesktop, refreshSession } from "./pairClient.js";

function jsonFetch(
  status: number,
  body: unknown,
  capture?: (url: string, init?: RequestInit) => void,
): typeof fetch {
  return (async (url: string, init?: RequestInit) => {
    capture?.(url, init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

test("pairDesktop: maps tokens + user + account and POSTs code + device_label", async () => {
  let captured: { url: string; body: string } | undefined;
  const fetchImpl = jsonFetch(
    200,
    {
      access_token: "at-1",
      refresh_token: "rt-1",
      token_type: "bearer",
      user: { user_id: "u1", discord_user_id: "d1", display_name: "Kostas" },
      account: {
        linked: true,
        puuid: "p1",
        game_name: "Chama",
        tag_line: "Shasu",
        region: "eun1",
        verified: true,
        rank_text: "Gold II, 45 LP",
      },
    },
    (url, init) => {
      captured = { url, body: String(init?.body) };
    },
  );

  const result = await pairDesktop(
    { code: "WXYZ-2345", deviceLabel: "Kostas' MacBook" },
    { fetch: fetchImpl, backendUrl: "http://x" },
  );

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.accessToken, "at-1");
    assert.equal(result.refreshToken, "rt-1");
    assert.equal(result.user.userId, "u1");
    assert.equal(result.user.discordUserId, "d1");
    assert.equal(result.user.displayName, "Kostas");
    assert.equal(result.account?.linked, true);
    assert.equal(result.account?.verified, true);
    assert.equal(result.account?.rankText, "Gold II, 45 LP");
  }
  assert.equal(captured?.url, "http://x/api/v1/auth/desktop-pair");
  const sent = JSON.parse(captured!.body) as Record<string, unknown>;
  assert.equal(sent["code"], "WXYZ-2345");
  assert.equal(sent["device_label"], "Kostas' MacBook");
});

test("pairDesktop: null account is preserved (no Riot link yet)", async () => {
  const result = await pairDesktop(
    { code: "WXYZ2345" },
    {
      fetch: jsonFetch(200, {
        access_token: "at",
        refresh_token: "rt",
        user: { user_id: "u", discord_user_id: "d", display_name: "n" },
        account: null,
      }),
      backendUrl: "http://x",
    },
  );
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.account, null);
  }
});

test("pairDesktop: 401 invalid/expired code maps to an error with the detail", async () => {
  const result = await pairDesktop(
    { code: "BADCODE0" },
    { fetch: jsonFetch(401, { detail: "invalid or expired code" }), backendUrl: "http://x" },
  );
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 401);
    assert.equal(result.detail, "invalid or expired code");
  }
});

test("pairDesktop: network failure maps to status 0", async () => {
  const throwing: typeof fetch = (async () => {
    throw new Error("down");
  }) as unknown as typeof fetch;
  const result = await pairDesktop({ code: "WXYZ2345" }, { fetch: throwing, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
  }
});

test("refreshSession: rotates tokens and POSTs refresh_token", async () => {
  let captured: { url: string; body: string } | undefined;
  const result = await refreshSession("rt-old", {
    fetch: jsonFetch(
      200,
      { access_token: "at-2", refresh_token: "rt-2", token_type: "bearer" },
      (url, init) => {
        captured = { url, body: String(init?.body) };
      },
    ),
    backendUrl: "http://x",
  });
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.accessToken, "at-2");
    assert.equal(result.refreshToken, "rt-2");
  }
  assert.equal(captured?.url, "http://x/api/v1/auth/refresh");
  assert.equal((JSON.parse(captured!.body) as Record<string, unknown>)["refresh_token"], "rt-old");
});

test("refreshSession: 401 (expired/revoked/reused) maps to an error", async () => {
  const result = await refreshSession("rt-stale", {
    fetch: jsonFetch(401, { detail: "refresh token revoked" }),
    backendUrl: "http://x",
  });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 401);
  }
});

test("refreshSession: network failure maps to status 0", async () => {
  const throwing: typeof fetch = (async () => {
    throw new Error("down");
  }) as unknown as typeof fetch;
  const result = await refreshSession("rt", { fetch: throwing, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
  }
});
