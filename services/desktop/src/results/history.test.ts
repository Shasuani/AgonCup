/**
 * Unit tests for the player match-history read (node:test; stubbed fetch, no
 * real network): URL + Bearer, snake_case -> camelCase mapping, error detail,
 * and the unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { listMyHistory } from "./history.js";

test("listMyHistory: GETs /results/history/mine with Bearer and maps the scoreboard", async () => {
  let captured: { url: string; method: string | undefined; headers: Record<string, string> } | undefined;
  const spyFetch: typeof fetch = (async (url: string, init?: RequestInit) => {
    captured = { url, method: init?.method, headers: (init?.headers ?? {}) as Record<string, string> };
    return new Response(
      JSON.stringify([
        {
          match_id: "m1",
          tournament_id: "t1",
          tournament_name: "Summer Cup",
          round: 2,
          lcu_game_id: "100",
          played_at: "2026-09-07T18:00:00+00:00",
          teams: [
            {
              is_winner: true,
              players: [
                { puuid: "me", display_name: "Chama", riot_id: "Chama#Shasu", champion_id: 103, kills: 7, deaths: 2, assists: 9, champion_name: "Ahri", level: 17, items: [3020, 3089, 0], spell1_id: 4, spell2_id: 14, gold_earned: 12000, minions_killed: 170, damage_to_champions: 21000, vision_score: 18, profile_icon_id: 4 },
              ],
            },
            { is_winner: false, players: [{ puuid: "p2", display_name: null, riot_id: null, champion_id: 5, kills: 1, deaths: 8, assists: 3 }] },
          ],
        },
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const result = await listMyHistory({ accessToken: "jwt" }, { fetch: spyFetch, backendUrl: "http://x" });

  assert.equal(captured?.url, "http://x/api/v1/results/history/mine");
  assert.equal(captured?.method, "GET");
  assert.equal(captured?.headers["Authorization"], "Bearer jwt");
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.games.length, 1);
    const g = result.games[0];
    assert.ok(g);
    assert.equal(g.tournamentName, "Summer Cup");
    assert.equal(g.round, 2);
    assert.equal(g.lcuGameId, "100");
    assert.equal(g.teams[0]?.isWinner, true);
    const me = g.teams[0]?.players[0];
    assert.ok(me);
    assert.equal(me.displayName, "Chama");
    assert.equal(me.championId, 103);
    assert.deepEqual([me.kills, me.deaths, me.assists], [7, 2, 9]);
    assert.equal(me.championName, "Ahri");
    assert.equal(me.level, 17);
    assert.deepEqual(me.items, [3020, 3089, 0]);
    assert.deepEqual([me.spell1Id, me.spell2Id], [4, 14]);
    assert.equal(me.goldEarned, 12000);
    const other = g.teams[1]?.players[0];
    assert.equal(other?.displayName, null);
    // Absent extras collapse to null / [] rather than NaN or undefined.
    assert.equal(other?.level, null);
    assert.deepEqual(other?.items, []);
  }
});

test("listMyHistory: error surfaces detail", async () => {
  const spyFetch: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "no acting guild" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await listMyHistory({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 400);
    assert.equal(result.detail, "no acting guild");
  }
});

test("listMyHistory: unreachable backend maps to status 0", async () => {
  const spyFetch: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await listMyHistory({ accessToken: "j" }, { fetch: spyFetch, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 0);
});
