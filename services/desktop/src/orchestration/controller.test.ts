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
    },
    async autoAcceptInvite() {
      calls.push("autoAcceptInvite");
      return () => calls.push("unsubInvite");
    },
    async getGameflowPhase() {
      return (over.phase as string) ?? "Lobby";
    },
    async captureEndOfGame() {
      calls.push("captureEndOfGame");
      return { source: "eogStatsBlock", lcuGameId: "g-1" } as never;
    },
    async captureLiveClientEvents() {
      return { source: "liveClientData", events: [] } as never;
    },
  };
  return { adapter: adapter as never, calls };
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

function createCmd(over: Partial<LobbyCommand> = {}): LobbyCommand {
  return {
    commandId: "c1",
    matchId: MATCH,
    hostEpoch: 0,
    kind: "create_lobby",
    spec: { matchId: MATCH, invitees: ["s1", "s2", "s3"] },
    ...(over as Record<string, unknown>),
  } as unknown as LobbyCommand;
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
    kind: "auto_accept",
    filter: { expectedInviterPuuid: "host-puuid", hostEpoch: 0 },
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

// Flush the microtask queue so fire-and-forget command handlers settle.
function tickMicrotasks(): Promise<void> {
  return new Promise((r) => setTimeout(r, 0));
}

async function runTickAsync(s: ReturnType<typeof makeScheduler>): Promise<void> {
  s.runTick();
  await tickMicrotasks();
}
