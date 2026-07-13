/**
 * Unit tests for the team reads/writes (node:test; stubbed fetch, no real
 * network). Covers success mapping (snake_case -> camelCase), the Bearer header,
 * request method/body/URL, error detail, 401 surfacing, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { createTeam, getTeamJoinCode, joinTeamByCode, listTeamMembers } from "./teams.js";

test("createTeam: POSTs {name} with Bearer and maps the returned join code", async () => {
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
        id: "team1",
        name: "Wolves",
        captain_user_id: "u1",
        guild_id: "g1",
        join_code: "WOLF-1234",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await createTeam({ accessToken: "jwt", name: "Wolves" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/teams");
  assert.equal(captured?.method, "POST");
  assert.deepEqual(captured?.body, { name: "Wolves" });
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.team.id, "team1");
    assert.equal(result.team.captainUserId, "u1");
    assert.equal(result.team.joinCode, "WOLF-1234");
  }
});

test("joinTeamByCode: POSTs {code} and maps the joined team", async () => {
  let captured: { url: string; body: unknown } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, body: init?.body ? JSON.parse(init.body as string) : undefined };
    return new Response(
      JSON.stringify({ id: "team2", name: "Foxes", captain_user_id: "u2", guild_id: "g2" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await joinTeamByCode({ accessToken: "j", code: "FOX-9" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/teams/join-by-code");
  assert.deepEqual(captured?.body, { code: "FOX-9" });
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.team.name, "Foxes");
});

test("joinTeamByCode: 404 surfaces bad code detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "bad code" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await joinTeamByCode({ accessToken: "j", code: "zzz" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 404);
    assert.equal(result.detail, "bad code");
  }
});

test("getTeamJoinCode: GETs the captain-only join code URL and maps it", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(JSON.stringify({ team_id: "team1", join_code: "WOLF-1234" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;

  const result = await getTeamJoinCode({ accessToken: "jwt", teamId: "team1" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/teams/team1/join-code");
  assert.equal(captured?.method, "GET");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.teamId, "team1");
    assert.equal(result.joinCode, "WOLF-1234");
  }
});

test("listTeamMembers: GETs the roster URL and maps rows", async () => {
  let captured: { url: string } | undefined;
  const spyFetch: typeof fetch = (async (url: string) => {
    captured = { url };
    return new Response(
      JSON.stringify([
        { team_id: "team1", user_id: "u1", role: "captain" },
        { team_id: "team1", user_id: "u2", role: "member" },
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await listTeamMembers({ accessToken: "j", teamId: "team1" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/teams/team1/members");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.members.length, 2);
    assert.equal(result.members[0]?.role, "captain");
    assert.equal(result.members[1]?.userId, "u2");
  }
});

test("401 is surfaced so the caller can refresh + retry", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await getTeamJoinCode({ accessToken: "j", teamId: "t" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
});

test("network failure maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await createTeam({ accessToken: "j", name: "x" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});
