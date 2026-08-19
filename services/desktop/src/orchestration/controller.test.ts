/**
 * Unit tests for the orchestration controller (node:test; no @lol/lcu at runtime
 * — its symbols are types only). Covers epoch fencing, the host create+invite
 * happy path, write-refusal soft-fail, guest auto-accept, foreign-match ignore,
 * and heartbeat -> single end-of-game report.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { OrchestrationController } from "./controller.js";
import type {
  BackendTransport,
  HostHeartbeat,
  LobbyAck,
  LobbyCommand,
  ResultReportMessage,
  Scheduler,
} from "./protocol.js";

const MATCH = "match-1";

function makeTransport() {
  const acks: LobbyAck[] = [];
  const heartbeats: HostHeartbeat[] = [];
  const reports: ResultReportMessage[] = [];
  let handler: ((c: LobbyCommand) => void) | null = null;
  const transport: BackendTransport = {
    onCommand(h) {
      handler = h;
      return () => {
        handler = null;
      };
    },
    async sendAck(a) {
      acks.push(a);
    },
    async sendHeartbeat(h) {
      heartbeats.push(h);
    },
    async sendReport(r) {
      reports.push(r);
    },
  };
  return {
    transport,
    acks,
    heartbeats,
    reports,
    emit: (c: LobbyCommand) => handler?.(c),
  };
}

function makeScheduler() {
  let tick: (() => void) | null = null;
  const scheduler: Scheduler = {
    every(_ms, fn) {
      tick = fn;
      return () => {
        tick = null;
      };
    },
  };
  return { scheduler, runTick: () => tick?.(), isCancelled: () => tick === null };
}

// A minimal fake LcuAdapter. Branded return types are cast (runtime is plain).
function makeAdapter(over: Partial<Record<string, unknown>> = {}) {
  const calls: string[] = [];
  const invited: string[] = [];
  let lobbyReads = 0;
  const adapter = {
    async readLockfileAuth() {
      return {} as never;
    },
    async createCustomLobby(spec: { matchId: string }) {
      calls.push("createCustomLobby");
      if (over.createThrows) throw new Error("LCU writes refused: kill-switch engaged");
      return { lobbyId: "lobby-xyz", lobbyName: `lol-${spec.matchId}`, hostEpoch: 1 } as never;
    },
    // Whether a lobby exists yet is what tells an invite from a WAIT: `noLobby`
    // is the manual path, where the human has not made the custom game yet.
    // `lobbyAppearsAfter` counts reads before it shows up, so a test can watch
    // the client notice it.
    async getCurrentLobby() {
      calls.push("getCurrentLobby");
      if (typeof over.lobbyAppearsAfter === "number") {
        if (lobbyReads++ < over.lobbyAppearsAfter) return null;
        return { lobbyId: "lobby-made-by-hand", isCustom: true } as never;
      }
      if (over.noLobby) return null;
      return { lobbyId: "lobby-xyz", isCustom: true } as never;
    },
    async invite(ids: readonly unknown[]) {
      calls.push(`invite:${ids.length}`);
      // Recorded verbatim: the backend orders the invite list deliberately
      // (own team, then the rival captain, then their roster) and a controller
      // that reordered or dropped ids would silently undo that.
      invited.push(...(ids as string[]));
    },
    async autoAcceptInvite() {
      calls.push("autoAcceptInvite");
      return () => calls.push("unsubInvite");
    },
    async getGameflowPhase() {
      if (over.phaseThrows) throw new Error("lcu unreachable");
      // Allow a sequence of phases so tests can walk InProgress -> EndOfGame.
      if (Array.isArray(over.phases) && over.phases.length > 0) {
        return (over.phases as string[]).shift() ?? "Lobby";
      }
      return (over.phase as string) ?? "Lobby";
    },
    async captureEndOfGame(_reporterPuuid?: unknown, liveClientEvents?: unknown) {
      calls.push("captureEndOfGame");
      // Echo the bundled live capture back so the test can assert it threads in.
      return {
        source: "eogStatsBlock",
        lcuGameId: "g-1",
        ...(liveClientEvents !== undefined ? { liveClientEvents } : {}),
      } as never;
    },
    async captureLiveClientEvents() {
      calls.push("captureLiveClientEvents");
      return {
        source: "liveClientData",
        events: [{ eventId: 1, eventName: "ChampionKill", eventTime: 12.3 }],
      } as never;
    },
  };
  return { adapter: adapter as never, calls, invited };
}

function makeController(
  t: ReturnType<typeof makeTransport>,
  a: ReturnType<typeof makeAdapter>,
  s: ReturnType<typeof makeScheduler>,
  heartbeatMs = 2000,
  // Movable so a test can walk past the manual-lobby wait's deadline.
  clock: { nowMs: () => number } = { nowMs: () => 1000 },
) {
  const controller = new OrchestrationController({
    adapter: a.adapter,
    transport: t.transport,
    scheduler: s.scheduler,
    clock,
    config: { matchId: MATCH, heartbeatMs },
  });
  // Host commands go to every client on the match channel and only the named
  // one acts (see `setAssignedHost`). Nearly every test below is about what THAT
  // client does, so it is the default here; the client that was NOT named has a
  // test of its own.
  controller.setAssignedHost(true);
  return controller;
}

/**
 * A create command in the EXACT shape the backend puts on the wire.
 *
 * These helpers used to invent their own shape (`kind: "create_lobby"`, a spec
 * holding `invitees`). Both sides then passed their own tests while agreeing on
 * nothing, which is how the contract drifted far enough that every real command
 * was dropped. Keep this mirroring `lol_realtime.CreateLobbyCommand`; the golden
 * fixture in `wireContract.test.ts` is what proves it still does.
 */
function createCmd(over: Partial<Record<string, unknown>> = {}): LobbyCommand {
  return {
    type: "create_lobby_command",
    commandId: "c1",
    matchId: MATCH,
    hostEpoch: 0,
    spec: wireSpec(),
    ...over,
  } as unknown as LobbyCommand;
}

function wireSpec(invitees: readonly string[] = ["s1", "s2", "s3"]): Record<string, unknown> {
  return {
    gameMode: "CLASSIC",
    mapId: 11,
    teamSize: 5,
    lobbyName: "LoL-abc12345",
    lobbyPassword: "ABC123",
    inviteeSummonerIds: invitees,
    inviteePuuids: invitees.map((i) => `puuid-${i}`),
  };
}

test("host create: creates lobby + invites + acks ok with lobby id", async () => {
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit(createCmd());
  await tickMicrotasks();
  assert.deepEqual(a.calls, ["createCustomLobby", "invite:3"]);
  assert.equal(t.acks.length, 1);
  assert.equal(t.acks[0]?.ok, true);
  assert.equal(t.acks[0]?.lcuLobbyId, "lobby-xyz");
});

test("write refused -> ack ok:false with reason (manual fallback), no throw", async () => {
  const t = makeTransport();
  const a = makeAdapter({ createThrows: true });
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit(createCmd());
  await tickMicrotasks();
  assert.equal(t.acks[0]?.ok, false);
  assert.match(t.acks[0]?.error ?? "", /kill-switch/);
});

test("stale epoch command is rejected", async () => {
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit(createCmd({ commandId: "c-new", hostEpoch: 5 }));
  await tickMicrotasks();
  t.emit(createCmd({ commandId: "c-old", hostEpoch: 3 }));
  await tickMicrotasks();
  const stale = t.acks.find((x) => x.commandId === "c-old");
  assert.equal(stale?.ok, false);
  assert.match(stale?.error ?? "", /stale/);
});

test("command for a different match is ignored (no ack)", async () => {
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit(createCmd({ matchId: "other-match" }));
  await tickMicrotasks();
  assert.equal(t.acks.length, 0);
  assert.equal(a.calls.length, 0);
});

test("guest auto-accept arms the filter and acks ok", async () => {
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit({
    commandId: "c2",
    matchId: MATCH,
    hostEpoch: 0,
    type: "auto_accept_command",
    expectedInviterPuuid: "host-puuid",
  } as unknown as LobbyCommand);
  await tickMicrotasks();
  assert.ok(a.calls.includes("autoAcceptInvite"));
  assert.equal(t.acks[0]?.ok, true);
});

test("heartbeat tick reports exactly once at EndOfGame", async () => {
  const t = makeTransport();
  const a = makeAdapter({ phase: "EndOfGame" });
  const s = makeScheduler();
  makeController(t, a, s).start();
  await runTickAsync(s);
  await runTickAsync(s); // second tick must NOT double-report
  assert.equal(t.heartbeats.length >= 1, true);
  assert.equal(t.heartbeats[0]?.phase, "EndOfGame");
  assert.equal(t.reports.length, 1);
  assert.equal(t.reports[0]?.report.lcuGameId, "g-1");
});

test("captures :2999 live events while InProgress and bundles them into the eog report", async () => {
  const t = makeTransport();
  // First tick: InProgress (captures live events); second tick: EndOfGame (reports).
  const a = makeAdapter({ phases: ["InProgress", "EndOfGame"] });
  const s = makeScheduler();
  makeController(t, a, s).start();
  await runTickAsync(s); // InProgress -> captureLiveClientEvents
  await runTickAsync(s); // EndOfGame  -> captureEndOfGame(bundle)
  assert.ok(a.calls.includes("captureLiveClientEvents"));
  assert.equal(t.reports.length, 1);
  const bundled = t.reports[0]?.report.liveClientEvents;
  assert.equal(bundled?.source, "liveClientData");
  assert.equal(bundled?.events[0]?.eventName, "ChampionKill");
});

// Flush the microtask queue so fire-and-forget command handlers settle.
function tickMicrotasks(): Promise<void> {
  return new Promise((r) => setTimeout(r, 0));
}

async function runTickAsync(s: ReturnType<typeof makeScheduler>): Promise<void> {
  s.runTick();
  await tickMicrotasks();
}

test("host create: invites the exact list the backend ordered, in order", async () => {
  // The backend sorts the roster deliberately — the host's own four teammates,
  // then the opposing captain, then their players — so ten invites do not land
  // as an unreadable jumble. The controller must pass that through untouched.
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  const ordered = ["mate-1", "mate-2", "mate-3", "mate-4", "rival-captain", "rival-1", "rival-2", "rival-3", "rival-4"];
  makeController(t, a, s).start();
  t.emit(createCmd({ spec: wireSpec(ordered) }));
  await tickMicrotasks();

  assert.deepEqual(a.invited, ordered);
  assert.equal(t.acks[0]?.ok, true);
});

test("host create: an empty invitee list is a lobby nobody can join", async () => {
  // Regression: the backend used to send `invitee_summoner_ids: []`, so the host
  // created the lobby and invited nobody. The controller cannot detect that on
  // its own — this pins the shape the backend must keep sending.
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit(createCmd({ spec: wireSpec([]) }));
  await tickMicrotasks();

  assert.deepEqual(a.calls, ["createCustomLobby", "invite:0"]);
  assert.deepEqual(a.invited, [], "nobody invited — what the bug looked like");
});

// --- the manual path: a human makes the lobby, the client fills it ---------- //

function inviteCmd(ids: readonly string[] = ["s1", "s2"]): LobbyCommand {
  return {
    type: "invite_players_command",
    commandId: "c-invite",
    matchId: MATCH,
    hostEpoch: 0,
    toSummonerIds: ids,
    toPuuids: ids.map((i) => `puuid-${i}`),
  } as unknown as LobbyCommand;
}

test("invite with no lobby yet: waits for the player to make one, then invites", async () => {
  // The manual fallback in full. Nobody's client took the host lease, so the
  // backend named one player, DMed them the name and password, and sent their
  // client this invite for a lobby that does not exist yet. Firing it straight
  // away fails against an empty client, and that failure used to be the end of
  // the story — which left nine people hunting for a custom game by name.
  const t = makeTransport();
  const a = makeAdapter({ lobbyAppearsAfter: 2 });
  const s = makeScheduler();
  makeController(t, a, s).start();

  t.emit(inviteCmd());
  await tickMicrotasks();
  assert.deepEqual(a.invited, [], "nothing to invite into yet");
  assert.equal(t.acks.length, 0, "not acked: the roster has not been invited");

  await runTickAsync(s); // heartbeat 1 — still no lobby
  assert.deepEqual(a.invited, []);

  await runTickAsync(s); // heartbeat 2 — the player just created it
  assert.deepEqual(a.invited, ["s1", "s2"], "the roster is invited the moment it exists");
  assert.equal(t.acks.length, 1);
  assert.equal(t.acks[0]?.ok, true);
});

test("the wait for a hand-made lobby does not invite twice", async () => {
  // The tick is a loop: without claiming the pending invite before sending it,
  // every subsequent tick sends the same invitations again and each player is
  // spammed once per heartbeat.
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();

  t.emit(inviteCmd());
  await tickMicrotasks();
  await runTickAsync(s);
  await runTickAsync(s);
  await runTickAsync(s);

  assert.equal(a.calls.filter((c) => c.startsWith("invite")).length, 1);
});

test("the wait is bounded: a lobby nobody ever makes is reported, not polled forever", async () => {
  const t = makeTransport();
  const a = makeAdapter({ noLobby: true });
  const s = makeScheduler();
  let now = 1000;
  makeController(t, a, s, 2000, { nowMs: () => now }).start();

  t.emit(inviteCmd());
  await tickMicrotasks();
  await runTickAsync(s);
  assert.equal(t.acks.length, 0, "still within the wait");

  now += 16 * 60_000; // past the 15-minute deadline
  await runTickAsync(s);
  assert.equal(t.acks.length, 1);
  assert.equal(t.acks[0]?.ok, false);
  assert.match(String(t.acks[0]?.error), /no lobby was created/);

  // ...and it stops looking.
  const reads = a.calls.filter((c) => c === "getCurrentLobby").length;
  await runTickAsync(s);
  assert.equal(a.calls.filter((c) => c === "getCurrentLobby").length, reads);
});

test("an abort while waiting cancels the pending invite", async () => {
  const t = makeTransport();
  const a = makeAdapter({ noLobby: true });
  const s = makeScheduler();
  makeController(t, a, s).start();

  t.emit(inviteCmd());
  await tickMicrotasks();
  t.emit({
    type: "abort_command",
    commandId: "c-abort",
    matchId: MATCH,
    hostEpoch: 0,
    reason: "cancelled",
  } as unknown as LobbyCommand);
  await tickMicrotasks();
  await runTickAsync(s);

  assert.deepEqual(a.invited, [], "a cancelled match must not invite anyone");
});

test("a client the backend did not name ignores host commands", async () => {
  // Commands are published to a per-match channel that all ten clients hear.
  // Before this, every one of them obeyed "create this lobby": ten custom games
  // for one match, each holding a single player, and ten sets of invitations.
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  const c = makeController(t, a, s);
  c.setAssignedHost(false);
  c.start();

  t.emit(createCmd());
  t.emit(inviteCmd());
  await tickMicrotasks();

  assert.deepEqual(a.calls, [], "no lobby, no invites, nothing");
  assert.deepEqual(t.acks, [], "and no ack — it was never addressed to us");

  // The guest half is still ours to do: accepting the invite is what makes us
  // join the one lobby that does exist.
  t.emit({
    type: "auto_accept_command",
    commandId: "c-accept",
    matchId: MATCH,
    hostEpoch: 0,
    expectedInviterPuuid: "puuid-host",
  } as unknown as LobbyCommand);
  await tickMicrotasks();
  assert.deepEqual(a.calls, ["autoAcceptInvite"]);
});

test("the host lease moving is picked up without rebuilding anything", async () => {
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  const c = makeController(t, a, s);
  c.setAssignedHost(false);
  c.start();

  t.emit(createCmd());
  await tickMicrotasks();
  assert.deepEqual(a.calls, []);

  c.setAssignedHost(true); // the next match poll says the lease is ours now
  t.emit(createCmd({ commandId: "c2" }));
  await tickMicrotasks();
  assert.deepEqual(a.calls, ["createCustomLobby", "invite:3"]);
});

test("a promotion acts — and acks — at the NEW epoch, not the superseded one", async () => {
  // The command's own `hostEpoch` is the epoch being superseded (that is what it
  // fences on). Acting at it meant the re-create's ack arrived at the deposed
  // epoch and was rejected server-side, so the LOBBY_CREATED transition was lost
  // and the next watchdog tick deposed the host again — straight to epoch
  // exhaustion and the manual fallback.
  const t = makeTransport();
  const a = makeAdapter();
  const s = makeScheduler();
  makeController(t, a, s).start();

  t.emit(createCmd()); // seeds #lastSpec at epoch 0
  await tickMicrotasks();
  t.emit({
    type: "promote_host_command",
    commandId: "c-promote",
    matchId: MATCH,
    hostEpoch: 0,
    newHostEpoch: 1,
    newHostPuuid: "puuid-me",
  } as unknown as LobbyCommand);
  await tickMicrotasks();

  assert.equal(t.acks.length, 2);
  assert.equal(t.acks[1]?.ok, true);
  assert.equal(t.acks[1]?.hostEpoch, 1);
  // Heartbeats now carry the adopted lease too — a beat at the superseded epoch
  // is fenced out server-side, which reads as a dead host.
  await runTickAsync(s);
  assert.equal(t.heartbeats.at(-1)?.hostEpoch, 1);
});

test("a superseded auto-accept arm tears its OWN loop down, not the winner's", async () => {
  // Commands dispatch fire-and-forget, so a replayed arm and a live one can
  // interleave. The loser used to stay armed forever: an orphan 2s poll that
  // could still accept. Now it finds itself superseded and unsubscribes itself.
  const t = makeTransport();
  const s = makeScheduler();
  const unsubs: string[] = [];
  let releaseFirst: ((u: () => void) => void) | null = null;
  const base = makeAdapter().adapter as Record<string, unknown>;
  const adapter = {
    ...base,
    autoAcceptInvite(): Promise<() => void> {
      if (releaseFirst === null) {
        // First arm: parked until the test releases it — AFTER the second won.
        return new Promise((res) => {
          releaseFirst = res;
        });
      }
      return Promise.resolve(() => unsubs.push("second"));
    },
  };
  const controller = new OrchestrationController({
    adapter: adapter as never,
    transport: t.transport,
    scheduler: s.scheduler,
    clock: { nowMs: () => 1000 },
    config: { matchId: MATCH, heartbeatMs: 2000 },
  });
  controller.start();
  const arm = (id: string) =>
    t.emit({
      type: "auto_accept_command",
      commandId: id,
      matchId: MATCH,
      hostEpoch: 0,
      expectedInviterPuuid: "puuid-host",
    } as unknown as LobbyCommand);

  arm("c1"); // parks inside the adapter
  await tickMicrotasks();
  arm("c2"); // wins: installs its loop
  await tickMicrotasks();
  // (Read through a variable: TS cannot see the closure assignment above.)
  const release = releaseFirst as ((u: () => void) => void) | null;
  release?.(() => unsubs.push("first")); // c1 finally resolves — too late
  await tickMicrotasks();

  assert.deepEqual(unsubs, ["first"]);
});

test("a replayed invite does not reset the manual-lobby wait deadline", async () => {
  // Every reconnect replays the invite command. Overwriting the parked deadline
  // each time turned the bounded 15-minute wait into an indefinite one.
  const t = makeTransport();
  const a = makeAdapter({ noLobby: true });
  const s = makeScheduler();
  let now = 0;
  makeController(t, a, s, 2000, { nowMs: () => now }).start();
  const inviteCmd = (id: string) =>
    ({
      type: "invite_players_command",
      commandId: id,
      matchId: MATCH,
      hostEpoch: 0,
      toSummonerIds: ["s1"],
      toPuuids: ["puuid-s1"],
    }) as unknown as LobbyCommand;

  t.emit(inviteCmd("c1")); // parked; deadline = 15min
  await tickMicrotasks();
  now = 14 * 60_000;
  t.emit(inviteCmd("c1-replay")); // must NOT move the deadline
  await tickMicrotasks();
  now = 16 * 60_000; // past the FIRST deadline
  await runTickAsync(s);

  assert.equal(t.acks.length, 1);
  assert.equal(t.acks[0]?.ok, false);
  assert.match(String(t.acks[0]?.error), /no lobby was created/);
});

test("a flaky phase read does not stall the parked manual-path invite", async () => {
  // The tick used to bail before the pending-invite retry when the gameflow
  // read failed — a broken phase endpoint silently froze the manual path.
  const t = makeTransport();
  const a = makeAdapter({ phaseThrows: true, lobbyAppearsAfter: 1 });
  const s = makeScheduler();
  makeController(t, a, s).start();
  t.emit({
    type: "invite_players_command",
    commandId: "c1",
    matchId: MATCH,
    hostEpoch: 0,
    toSummonerIds: ["s1", "s2"],
    toPuuids: ["p1", "p2"],
  } as unknown as LobbyCommand);
  await tickMicrotasks(); // parked (no lobby yet)
  await runTickAsync(s); // phase read throws; lobby appears on this retry's read
  assert.ok(a.calls.includes("invite:2"));
  assert.equal(t.acks[0]?.ok, true);
});
