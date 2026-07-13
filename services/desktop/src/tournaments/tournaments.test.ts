/**
 * Unit tests for the tournament reads/writes (node:test; stubbed fetch, no real
 * network). Covers success mapping (snake_case -> camelCase), the Bearer header,
 * request method/body, error detail, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { joinTournamentByCode, listMyTournaments } from "./tournaments.js";

test("listMyTournaments: GETs /tournaments/mine with Bearer and maps rows", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = {
      url,
      method: init?.method,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    return new Response(
      JSON.stringify([
        {
          id: "t1",
          name: "Summer Cup",
          status: "open",
          format: "single_elim",
          game_mode: "aram_1v1",
          best_of: 3,
          max_teams: 16,
          is_shared_to_central: true,
        },
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await listMyTournaments({ accessToken: "jwt" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/tournaments/mine");
  assert.equal(captured?.method, "GET");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.tournaments.length, 1);
    const t = result.tournaments[0];
    assert.ok(t);
    assert.equal(t.id, "t1");
    assert.equal(t.gameMode, "aram_1v1");
    assert.equal(t.bestOf, 3);
    assert.equal(t.maxTeams, 16);
    assert.equal(t.isSharedToCentral, true);
  }
});

test("listMyTournaments: error surfaces detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "nope" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await listMyTournaments({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 403);
    assert.equal(result.detail, "nope");
  }
});

test("joinTournamentByCode: POSTs code + Bearer and maps the tournament", async () => {
  let captured: { url: string; method: string | undefined; body: unknown; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = {
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(init.body as string) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    return new Response(
      JSON.stringify({
        id: "t9",
        name: "Joined",
        status: "open",
        format: "double_elim",
        game_mode: "summoners_rift",
        best_of: 1,
        max_teams: 8,
        is_shared_to_central: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await joinTournamentByCode(
    { accessToken: "jwt", code: "ABCD" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );

  assert.equal(captured?.url, "http://x/api/v1/tournaments/join-by-code");
  assert.equal(captured?.method, "POST");
  assert.deepEqual(captured?.body, { code: "ABCD" });
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(captured?.headers["Content-Type"], "application/json");
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.tournament.id, "t9");
});

test("joinTournamentByCode: 404 surfaces invalid join code", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "invalid join code" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await joinTournamentByCode({ accessToken: "j", code: "ZZ" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 404);
    assert.equal(result.detail, "invalid join code");
  }
});

test("401 is surfaced so the caller can refresh + retry", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await listMyTournaments({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
});

test("network failure maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await joinTournamentByCode({ accessToken: "j", code: "x" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});
