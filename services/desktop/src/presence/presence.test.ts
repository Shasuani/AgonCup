/**
 * Unit tests for the global live-presence read (node:test; stubbed fetch, no
 * real network). Covers success mapping (snake_case -> camelCase), the Bearer
 * header, empty presence, error detail, 401 surfacing, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { getLiveBoard, getPresence } from "./presence.js";

test("getPresence: GETs /presence with Bearer and maps the payload", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(
      JSON.stringify({
        players_in_game: 3,
        entries: [
          {
            match_id: "m1",
            guild_name: "Chama's Server",
            tournament_name: "Summer Cup",
            game_mode: "aram_1v1",
            started_at: "2026-07-04T10:00:00Z",
            players: ["Chama", "Rival"],
          },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await getPresence({ accessToken: "jwt" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/presence");
  assert.equal(captured?.method, "GET");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.playersInGame, 3);
    assert.equal(result.entries.length, 1);
    const e = result.entries[0];
    assert.ok(e);
    assert.equal(e.matchId, "m1");
    assert.equal(e.guildName, "Chama's Server");
    assert.equal(e.tournamentName, "Summer Cup");
    assert.equal(e.gameMode, "aram_1v1");
    assert.equal(e.startedAt, "2026-07-04T10:00:00Z");
    assert.deepEqual(e.players, ["Chama", "Rival"]);
  }
});

test("getPresence: empty presence returns 0 players + no entries", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ players_in_game: 0, entries: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await getPresence({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.playersInGame, 0);
    assert.equal(result.entries.length, 0);
  }
});

test("getPresence: error surfaces detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "boom" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await getPresence({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 500);
    assert.equal(result.detail, "boom");
  }
});

test("401 is surfaced so the caller can refresh + retry", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await getPresence({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
});

test("network failure maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await getPresence({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});

test("getLiveBoard: GETs /presence/live/{matchId} with Bearer and camelises the board", async () => {
  let url = "";
  let auth = "";
  const spyFetch: typeof fetch = (async (u: string, init?: RequestInit) => {
    url = u;
    auth = ((init?.headers ?? {}) as Record<string, string>)["Authorization"] ?? "";
    return new Response(
      JSON.stringify({
        match_id: "m 1",
        game_time: 754.2,
        game_mode: "CLASSIC",
        updated_at: "2026-09-14T10:00:00Z",
        players: [
          {
            name: "Alice", champion_key: "MissFortune", champion_name: "Miss Fortune", team: "CHAOS",
            level: 9, is_dead: true, respawn_timer: 17, items: [1055, 0, 3006, 0, 0, 0, 3340],
            kills: 4, deaths: 2, assists: 7, creep_score: 110, ward_score: 6.4,
            spell1_key: "SummonerFlash", spell2_key: null,
          },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const r = await getLiveBoard({ accessToken: "jwt", matchId: "m 1" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(url, "http://x/api/v1/presence/live/m%201");
  assert.equal(auth, "Bearer jwt");
  assert.ok(r.ok);
  if (r.ok) {
    assert.equal(r.board.gameTime, 754.2);
    assert.deepEqual(r.board.players[0], {
      name: "Alice", championKey: "MissFortune", championName: "Miss Fortune", team: "CHAOS",
      level: 9, isDead: true, respawnTimer: 17, items: [1055, 0, 3006, 0, 0, 0, 3340],
      kills: 4, deaths: 2, assists: 7, creepScore: 110, wardScore: 6.4,
      spell1Key: "SummonerFlash", spell2Key: null,
    });
  }
});

test("getLiveBoard: 404 (not live / nothing yet) and an unreachable backend map to ok:false", async () => {
  const notFound = (async () =>
    new Response(JSON.stringify({ detail: "no live scoreboard yet" }), { status: 404 })) as unknown as typeof fetch;
  const r = await getLiveBoard({ accessToken: "jwt", matchId: "m1" }, { fetch: notFound, backendUrl: "http://x" });
  assert.deepEqual(r, { ok: false, status: 404, detail: "no live scoreboard yet" });

  const down = (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch;
  const d = await getLiveBoard({ accessToken: "jwt", matchId: "m1" }, { fetch: down, backendUrl: "http://x" });
  assert.equal(d.ok, false);
  if (!d.ok) assert.equal(d.status, 0);
});
