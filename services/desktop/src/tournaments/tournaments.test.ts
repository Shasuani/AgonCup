/**
 * Unit tests for the tournament reads/writes (node:test; stubbed fetch, no real
 * network). Covers success mapping (snake_case -> camelCase), the Bearer header,
 * request method/body, error detail, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { getBracket, joinTournamentByCode, listMyTournaments, markReady } from "./tournaments.js";

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

test("getBracket: maps match id + ready-gate fields", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(
      JSON.stringify({
        tournament: { id: "t1", name: "Cup", status: "running" },
        matches: [
          {
            id: "m1",
            round: 0,
            slot: 0,
            team_a_id: "A",
            team_a_name: "Alpha",
            team_b_id: "B",
            team_b_name: "Bravo",
            winner_team_id: null,
            status: "pending",
            ready_deadline_at: "2026-08-01T12:05:00+00:00",
            team_a_ready: true,
            team_b_ready: false,
          },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as unknown as typeof fetch;

  const result = await getBracket(
    { accessToken: "j", tournamentId: "t1" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );
  assert.equal(result.ok, true);
  if (result.ok) {
    const m = result.matches[0];
    assert.ok(m);
    assert.equal(m.id, "m1");
    assert.equal(m.readyDeadlineAt, "2026-08-01T12:05:00+00:00");
    assert.equal(m.teamAReady, true);
    assert.equal(m.teamBReady, false);
  }
});

test("markReady: POSTs /matches/{id}/ready with Bearer and maps both_ready", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(JSON.stringify({ match_id: "m1", side: "team_a", both_ready: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;

  const result = await markReady({ accessToken: "jwt", matchId: "m1" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/tournaments/matches/m1/ready");
  assert.equal(captured?.method, "POST");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.side, "team_a");
    assert.equal(result.bothReady, true);
  }
});

test("markReady: 403 surfaces the not-captain detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "you do not captain a team in this match" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await markReady({ accessToken: "j", matchId: "m1" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 403);
});
