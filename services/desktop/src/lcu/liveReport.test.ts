/**
 * Unit tests for the MID-GAME (first-blood) report's scoreboard (node:test; stub
 * LcuRuntime, no real League client / network). A 1v1 duel never produces an eog
 * block, so this report is the only thing the match history can show for it
 * (ToDoList #9/#10): champion, items, level and K/D/A per Riot ID, with the
 * first-blood scorer's side marked as the winner.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LcuAdapterImpl,
  type HttpRequest,
  type HttpResponse,
  type LcuAdapterConfig,
  type LcuRuntime,
  type LcuSchemaVersion,
  type LiveClientEventCapture,
} from "@lol/lcu";

const LOCKFILE_CONTENTS = "LeagueClient:12345:54321:secretpw:https";

const READ_ONLY_CONFIG: LcuAdapterConfig = {
  writeAutomationOptIn: false,
  killSwitchEngaged: true,
  requirePresenceForWrites: true,
  pacing: { minWriteDelayMs: 0, writeJitterMs: 0, maxWritesPerMinute: 0, maxBackoffMs: 0, maxRetryAttempts: 0 },
  schemaVersion: "0.0.0-test" as LcuSchemaVersion,
};

function stubRuntime(
  respond: (req: HttpRequest) => HttpResponse,
  requests: HttpRequest[] = [],
): LcuRuntime {
  return {
    http: {
      async request(req: HttpRequest): Promise<HttpResponse> {
        requests.push(req);
        return respond(req);
      },
    },
    fs: { readLockfile: async () => ({ contents: LOCKFILE_CONTENTS, mtimeMs: 1 }) },
    clock: { nowMs: () => 1_700_000_000_000 },
    sleeper: { sleep: async () => {} },
    random: { next: () => 0 },
  };
}

/** One `allPlayers[]` line as the documented `:2999` API ships it. */
function livePlayer(args: {
  gameName: string;
  tagLine: string;
  champion: string;
  team: "ORDER" | "CHAOS";
  kda: [number, number, number];
  items: number[];
}): Record<string, unknown> {
  return {
    riotId: `${args.gameName}#${args.tagLine}`,
    riotIdGameName: args.gameName,
    riotIdTagLine: args.tagLine,
    summonerName: args.gameName,
    championName: args.champion,
    rawChampionName: `game_character_displayname_${args.champion.replace(/\s/g, "")}`,
    team: args.team,
    level: 6,
    isDead: false,
    respawnTimer: 0,
    items: args.items.map((itemID, slot) => ({ itemID, slot, count: 1 })),
    scores: { kills: args.kda[0], deaths: args.kda[1], assists: args.kda[2], creepScore: 21, wardScore: 1.6 },
    summonerSpells: {
      summonerSpellOne: { rawDisplayName: "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName" },
      summonerSpellTwo: { rawDisplayName: "GeneratedTip_SummonerSpell_SummonerDot_DisplayName" },
    },
  };
}

const ALL_PLAYERS = [
  // Red side listed first on purpose: the report must still put blue first.
  livePlayer({ gameName: "Rival", tagLine: "EUW", champion: "Miss Fortune", team: "CHAOS", kda: [0, 1, 0], items: [1055, 3006] }),
  livePlayer({ gameName: "Shasuani", tagLine: "EUNE", champion: "Ahri", team: "ORDER", kda: [1, 0, 0], items: [1056, 3020, 0] }),
];

const KILL_STREAM = [
  { EventID: 0, EventName: "GameStart", EventTime: 0 },
  { EventID: 1, EventName: "ChampionKill", EventTime: 83.1, KillerName: "Shasuani", VictimName: "Rival", Assisters: [] },
  { EventID: 2, EventName: "FirstBlood", EventTime: 83.1, Recipient: "Shasuani" },
];

function json(body: unknown): HttpResponse {
  return { status: 200, body: JSON.stringify(body) };
}

function responder(overrides: {
  allgamedata?: HttpResponse;
  championSummary?: HttpResponse;
  events?: unknown[];
} = {}): (req: HttpRequest) => HttpResponse {
  return (req) => {
    if (/lol-gameflow\/v1\/session$/.test(req.url)) return json({ gameData: { gameId: 7001 } });
    if (/liveclientdata\/eventdata/.test(req.url)) return json({ Events: overrides.events ?? KILL_STREAM });
    if (/liveclientdata\/allgamedata/.test(req.url)) {
      return overrides.allgamedata ?? json({ gameData: { gameMode: "ARAM", gameTime: 90 }, allPlayers: ALL_PLAYERS });
    }
    if (/champion-summary\.json$/.test(req.url)) {
      return (
        overrides.championSummary ??
        json([{ id: -1, name: "None" }, { id: 103, name: "Ahri" }, { id: 21, name: "Miss Fortune" }])
      );
    }
    return { status: 404, body: "{}" };
  };
}

test("first-blood report: the live scoreboard rides along, blue side first, scorer's side wins", async () => {
  const adapter = new LcuAdapterImpl({ config: READ_ONLY_CONFIG, runtime: stubRuntime(responder()) });

  const report = await adapter.captureLiveClientReport();

  assert.equal(report.source, "liveClientData");
  assert.equal(report.lcuGameId, "7001");
  assert.equal(report.teams.length, 2);
  const [blue, red] = report.teams;
  assert.equal(blue?.isWinningTeam, true);
  assert.equal(red?.isWinningTeam, false);
  const me = blue?.players[0];
  assert.deepEqual(me, {
    riotId: "Shasuani#EUNE",
    championId: 103, // from the client's own catalogue, by name
    championsKilled: 1,
    numDeaths: 0,
    assists: 0,
    championName: "Ahri",
    level: 6,
    items: [1056, 3020, 0, 0, 0, 0, 0],
    minionsKilled: 21,
    visionScore: 2,
  });
  assert.equal(red?.players[0]?.riotId, "Rival#EUW");
  assert.equal(red?.players[0]?.championId, 21);
  // The live client exposes no PUUID: none is invented.
  assert.equal("puuid" in (me ?? {}), false);
});

test("first-blood report: an unreadable scoreboard leaves teams empty but the report still goes out", async () => {
  const adapter = new LcuAdapterImpl({
    config: READ_ONLY_CONFIG,
    runtime: stubRuntime(responder({ allgamedata: { status: 500, body: "" } })),
  });

  const report = await adapter.captureLiveClientReport();

  assert.equal(report.lcuGameId, "7001");
  assert.deepEqual(report.teams, []);
  assert.equal(report.liveClientEvents?.events.length, 3);
});

test("first-blood report: an unreadable champion catalogue keeps the name and reports id 0", async () => {
  const adapter = new LcuAdapterImpl({
    config: READ_ONLY_CONFIG,
    runtime: stubRuntime(responder({ championSummary: { status: 500, body: "" } })),
  });

  const report = await adapter.captureLiveClientReport();

  assert.equal(report.teams[0]?.players[0]?.championId, 0);
  assert.equal(report.teams[0]?.players[0]?.championName, "Ahri");
});

test("first-blood report: no FirstBlood event yet -> no side is marked as the winner", async () => {
  const adapter = new LcuAdapterImpl({ config: READ_ONLY_CONFIG, runtime: stubRuntime(responder()) });
  const noKills: LiveClientEventCapture = {
    source: "liveClientData",
    events: [{ eventId: 0, eventName: "GameStart", eventTime: 0 }],
    schemaVersion: READ_ONLY_CONFIG.schemaVersion,
  };

  const report = await adapter.captureLiveClientReport(undefined, noKills);

  assert.deepEqual(report.teams.map((t) => t.isWinningTeam), [false, false]);
  assert.equal(report.teams[0]?.players.length, 1);
});

test("first-blood report: without a Recipient the scorer is the paired ChampionKill's killer (Name#TAG form)", async () => {
  const adapter = new LcuAdapterImpl({
    config: READ_ONLY_CONFIG,
    runtime: stubRuntime(
      responder({
        events: [
          { EventID: 1, EventName: "ChampionKill", EventTime: 60.0, KillerName: "Minion", VictimName: "Shasuani" },
          { EventID: 2, EventName: "ChampionKill", EventTime: 83.1, KillerName: "Rival#EUW", VictimName: "Shasuani" },
          { EventID: 3, EventName: "FirstBlood", EventTime: 83.2 },
        ],
      }),
    ),
  });

  const report = await adapter.captureLiveClientReport();

  // Red side (CHAOS) scored: the earlier minion kill is not first blood.
  assert.deepEqual(report.teams.map((t) => t.isWinningTeam), [false, true]);
});

test("first-blood report: the champion catalogue is read once per adapter", async () => {
  const requests: HttpRequest[] = [];
  const adapter = new LcuAdapterImpl({ config: READ_ONLY_CONFIG, runtime: stubRuntime(responder(), requests) });

  await adapter.captureLiveClientReport();
  await adapter.captureLiveClientReport();

  assert.equal(requests.filter((r) => /champion-summary\.json$/.test(r.url)).length, 1);
});
