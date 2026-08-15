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
  const adapter = {
    async readLockfileAuth() {
      return {} as never;
    },
    async createCustomLobby(spec: { matchId: string }) {
      calls.push("createCustomLobby");
      if (over.createThrows) throw new Error("LCU writes refused: kill-switch engaged");
      return { lobbyId: "lobby-xyz", lobbyName: `lol-${spec.matchId}`, hostEpoch: 1 } as never;
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

function makeController(t: ReturnType<typeof makeTransport>, a: ReturnType<typeof makeAdapter>, s: ReturnType<typeof makeScheduler>, heartbeatMs = 2000) {
  return new OrchestrationController({
    adapter: a.adapter,
    transport: t.transport,
    scheduler: s.scheduler,
    clock: { nowMs: () => 1000 },
    config: { matchId: MATCH, heartbeatMs },
  });
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
