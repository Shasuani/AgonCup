/**
 * Unit tests for the live-game winner poller (node:test; injected reads, no real
 * LCU/:2999). Covers the duel-deciding first-blood banner (Recipient and the
 * paired-kill fallback), the GameEnd win/loss verdict, the port-dies-at-game-exit
 * freeze, banner lifetime across phases (held through Lobby, cleared by a new
 * cycle), and the change-only emission dedupe.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  GameflowPhase,
  LiveClientEventCapture,
  LiveClientKillEvent,
  LiveGamePlayer,
  LiveGameSnapshot,
} from "@lol/lcu";
import { LiveGamePoller, type LiveGameView } from "./liveGamePoller.js";

const SCHEMA = "test" as LiveClientEventCapture["schemaVersion"];

function capture(events: LiveClientKillEvent[]): LiveClientEventCapture {
  return { source: "liveClientData", events, schemaVersion: SCHEMA };
}

function player(
  riotId: string,
  team: "ORDER" | "CHAOS",
  extra: Partial<LiveGamePlayer> & { noSpells?: boolean } = {},
): LiveGamePlayer {
  const { noSpells, ...rest } = extra;
  return {
    riotId,
    championKey: "Ahri",
    championName: "Ahri",
    team,
    level: 6,
    isDead: false,
    respawnTimer: 0,
    items: [1055, 0, 0, 0, 0, 0, 3340],
    kills: 1,
    deaths: 0,
    assists: 0,
    creepScore: 40,
    wardScore: 0.5,
    ...(noSpells ? {} : { spell1Key: "SummonerFlash" }),
    ...rest,
  };
}

/** Mutable cells one poller reads from; tests script them between ticks. */
interface Cells {
  phase: GameflowPhase;
  events: LiveClientKillEvent[];
  snapshot: LiveGameSnapshot | null;
  eventsFail: boolean;
  snapshotFail: boolean;
  phaseFail: boolean;
  localName: string | null;
  seen: Array<LiveGameView | null>;
}

function build(local: string | null = "Chama"): { poller: LiveGamePoller; cells: Cells } {
  const cells: Cells = {
    phase: "None",
    events: [],
    snapshot: null,
    eventsFail: false,
    snapshotFail: false,
    phaseFail: false,
    localName: local,
    seen: [],
  };
  const poller = new LiveGamePoller({
    readPhase: async () => {
      if (cells.phaseFail) throw new Error("lcu down");
      return cells.phase;
    },
    readEvents: async () => {
      if (cells.eventsFail) throw new Error("port dead");
      return capture(cells.events);
    },
    readSnapshot: async () => {
      if (cells.snapshotFail || cells.snapshot === null) throw new Error("port dead");
      return cells.snapshot;
    },
    readLocalGameName: async () => cells.localName,
    onUpdate: (v) => cells.seen.push(v),
  });
  return { poller, cells };
}

const FB_RECIPIENT: LiveClientKillEvent = {
  eventId: 2,
  eventName: "FirstBlood",
  eventTime: 83.2,
  recipient: "Chama",
};

test("no game: emits null once (an explicit nothing-to-show), stays silent after", async () => {
  const { poller, cells } = build();
  await poller.tick();
  await poller.tick();
  assert.equal(poller.lastView, null);
  assert.deepEqual(cells.seen, [null]); // same contract as the champ-select poller
});

test("first blood via Recipient decides the banner while the game still runs", async () => {
  const { poller, cells } = build("Chama");
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  const view = await poller.tick();
  assert.deepEqual(view, {
    status: "in_game",
    firstBlood: { scorer: "Chama", isYou: true, atSeconds: 83.2 },
    result: null,
    board: null,
  });
});

test("scorer matching survives the GameName#TAG form on either side", async () => {
  const { poller, cells } = build("Chama#Shasu");
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT]; // stream says bare "Chama"
  const view = await poller.tick();
  assert.equal(view?.firstBlood?.isYou, true);
});

test("without Recipient the paired ChampionKill names the scorer; a later kill never does", async () => {
  const { poller, cells } = build("Bob");
  cells.phase = "InProgress";
  cells.events = [
    { eventId: 1, eventName: "ChampionKill", eventTime: 83.0, killerName: "Alice", victimName: "Bob" },
    { eventId: 2, eventName: "FirstBlood", eventTime: 83.2 },
    // An unrelated kill much later must not become "first blood".
    { eventId: 9, eventName: "ChampionKill", eventTime: 412.0, killerName: "Bob", victimName: "Alice" },
  ];
  const view = await poller.tick();
  assert.deepEqual(view?.firstBlood, { scorer: "Alice", isYou: false, atSeconds: 83.2 });
});

test("GameEnd flips the view to ended with the local verdict", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [
    FB_RECIPIENT,
    { eventId: 7, eventName: "GameEnd", eventTime: 1201.5, result: "Win" },
  ];
  const view = await poller.tick();
  assert.equal(view?.status, "ended");
  assert.equal(view?.result, "win");
});

test("the port dying at game exit freezes the last capture as the final banner", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  await poller.tick();

  cells.phase = "EndOfGame";
  cells.eventsFail = true; // :2999 is gone
  const view = await poller.tick();
  assert.equal(view?.status, "ended");
  assert.equal(view?.firstBlood?.scorer, "Chama");
});

test("the banner survives the trip back to the lobby, and a new cycle clears it", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT, { eventId: 7, eventName: "GameEnd", eventTime: 1201.5, result: "Lose" }];
  await poller.tick();

  cells.phase = "Lobby"; // players land here seconds after the game ends
  const held = await poller.tick();
  assert.equal(held?.status, "ended");
  assert.equal(held?.result, "loss");

  cells.phase = "ChampSelect"; // the NEXT game is forming
  const cleared = await poller.tick();
  assert.equal(cleared, null);
  assert.equal(poller.lastView, null);
});

test("a game that vanishes without an end phase (dodge/remake) is frozen, not kept live", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  await poller.tick();
  cells.phase = "None";
  const view = await poller.tick();
  assert.equal(view?.status, "ended");
});

test("an unreadable client holds the current view instead of wiping it", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  await poller.tick();
  cells.phaseFail = true;
  const view = await poller.tick();
  assert.equal(view?.firstBlood?.scorer, "Chama");
});

test("an unchanged view is emitted once, not per tick", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  await poller.tick();
  await poller.tick();
  await poller.tick();
  assert.equal(cells.seen.length, 1);
});

test("the scoreboard rides the view: champions, items, spells, K/D/A, and which row is you", async () => {
  const { poller, cells } = build("Chama#Shasu");
  cells.phase = "InProgress";
  cells.snapshot = {
    gameTime: 312.4,
    gameMode: "ARAM",
    players: [player("Chama#Shasu", "ORDER"), player("Alice#EUW", "CHAOS", { championKey: "Zed", noSpells: true })],
  };
  const view = await poller.tick();
  assert.equal(view?.status, "in_game");
  assert.equal(view?.board?.gameTime, 312.4);
  assert.equal(view?.board?.gameMode, "ARAM");
  assert.deepEqual(view?.board?.players, [
    {
      name: "Chama#Shasu",
      championKey: "Ahri",
      championName: "Ahri",
      team: "ORDER",
      level: 6,
      isDead: false,
      respawnTimer: 0,
      items: [1055, 0, 0, 0, 0, 0, 3340],
      kills: 1,
      deaths: 0,
      assists: 0,
      creepScore: 40,
      wardScore: 0.5,
      spell1Key: "SummonerFlash",
      spell2Key: null,
      isYou: true,
    },
    {
      name: "Alice#EUW",
      championKey: "Zed",
      championName: "Ahri",
      team: "CHAOS",
      level: 6,
      isDead: false,
      respawnTimer: 0,
      items: [1055, 0, 0, 0, 0, 0, 3340],
      kills: 1,
      deaths: 0,
      assists: 0,
      creepScore: 40,
      wardScore: 0.5,
      spell1Key: null,
      spell2Key: null,
      isYou: false,
    },
  ]);
});

test("a fresher scoreboard is emitted every tick, and a blip holds the last one", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.snapshot = { gameTime: 10, gameMode: "CLASSIC", players: [player("Chama", "ORDER")] };
  await poller.tick();
  cells.snapshot = { gameTime: 13, gameMode: "CLASSIC", players: [player("Chama", "ORDER", { kills: 2 })] };
  await poller.tick();
  assert.equal(cells.seen.length, 2);
  assert.equal(poller.lastView?.board?.players[0]?.kills, 2);

  cells.snapshotFail = true; // :2999 hiccup mid-game
  const held = await poller.tick();
  assert.equal(held?.board?.gameTime, 13);
  assert.equal(cells.seen.length, 2); // nothing new to show
});

test("the final scoreboard is frozen when the game ends, and cleared by a new cycle", async () => {
  const { poller, cells } = build();
  cells.phase = "InProgress";
  cells.snapshot = { gameTime: 900, gameMode: "CLASSIC", players: [player("Chama", "ORDER", { kills: 7 })] };
  await poller.tick();

  cells.phase = "EndOfGame";
  cells.snapshotFail = true; // the port is gone
  const ended = await poller.tick();
  assert.equal(ended?.status, "ended");
  assert.equal(ended?.board?.players[0]?.kills, 7);

  cells.phase = "ChampSelect";
  const cleared = await poller.tick();
  assert.equal(cleared, null);
});

test("an unknown local name yields isYou:null, and heals once readable", async () => {
  const { poller, cells } = build(null);
  cells.phase = "InProgress";
  cells.events = [FB_RECIPIENT];
  const first = await poller.tick();
  assert.equal(first?.firstBlood?.isYou, null);
  cells.localName = "Chama";
  const second = await poller.tick();
  assert.equal(second?.firstBlood?.isYou, true);
});
