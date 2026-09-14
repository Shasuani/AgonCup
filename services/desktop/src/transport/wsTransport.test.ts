/**
 * Unit tests for the WebSocket BackendTransport (node:test; fake socket + fake
 * timers, NO real WS). Covers: sends the subscribe frame as the first frame on
 * open; parses inbound command frames and fans them to handlers; ack/heartbeat/
 * report serialisation + queue/last-write semantics; and reconnect-on-drop with
 * re-subscribe. Importing the module opens no socket.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  WsBackendTransport,
  WS_CLOSE_AUTH,
  WS_OPEN,
  WS_CLOSED,
  type Timers,
  type WebSocketLike,
} from "./wsTransport.js";
import type { LobbyCommand } from "../orchestration/protocol.js";

/** A controllable fake `ws` socket: tests drive open/message/close manually. */
class FakeSocket implements WebSocketLike {
  readyState: number = WS_OPEN;
  readonly sent: string[] = [];
  readonly url: string;
  #listeners: Record<string, ((...args: never[]) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code?: number): void {
    this.readyState = WS_CLOSED;
    this.emit("close", code ?? 1000);
  }

  on(event: string, listener: (...args: never[]) => void): void {
    (this.#listeners[event] ??= []).push(listener);
  }

  emit(event: string, ...args: unknown[]): void {
    for (const l of this.#listeners[event] ?? []) (l as (...a: unknown[]) => void)(...args);
  }

  // Test helpers
  fireOpen(): void {
    this.emit("open");
  }
  fireMessage(obj: unknown): void {
    this.emit("message", JSON.stringify(obj));
  }
  fireRaw(text: string): void {
    this.emit("message", text);
  }
}

/** Fake timers: capture the scheduled reconnect callback and fire it on demand. */
function makeTimers() {
  let pending: (() => void) | null = null;
  let lastDelay = -1;
  const timers: Timers = {
    setTimeout(fn, ms) {
      pending = fn;
      lastDelay = ms;
      return { id: 1 };
    },
    clearTimeout() {
      pending = null;
    },
  };
  return {
    timers,
    fire: () => {
      const fn = pending;
      pending = null;
      fn?.();
    },
    lastDelay: () => lastDelay,
    hasPending: () => pending !== null,
  };
}

function makeTransport(
  over: {
    onWarn?: (e: string, d: Record<string, unknown>) => void;
    guildId?: string;
    onAuthRejected?: () => void;
    tokenProvider?: () => string;
  } = {},
) {
  const sockets: FakeSocket[] = [];
  const { timers, fire, lastDelay, hasPending } = makeTimers();
  const transport = new WsBackendTransport(
    {
      url: "wss://host/api/v1/realtime/ws",
      token: "jwt-abc",
      matchId: "match-1",
      reconnect: { baseDelayMs: 500, maxDelayMs: 30_000, factor: 2, jitter: 0 },
      ...(over.onWarn ? { onWarn: over.onWarn } : {}),
      ...(over.guildId ? { guildId: over.guildId } : {}),
      ...(over.onAuthRejected ? { onAuthRejected: over.onAuthRejected } : {}),
      ...(over.tokenProvider ? { tokenProvider: over.tokenProvider } : {}),
    },
    (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    timers,
  );
  return { transport, sockets, fire, lastDelay, hasPending };
}

test("connect sends subscribe as the first frame, with the token in the URL", () => {
  const { transport, sockets } = makeTransport();
  transport.connect();
  const sock = sockets[0]!;
  assert.match(sock.url, /token=jwt-abc/);
  assert.equal(sock.sent.length, 0); // nothing before open
  sock.fireOpen();
  assert.equal(sock.sent.length, 1);
  assert.deepEqual(JSON.parse(sock.sent[0]!), { type: "subscribe", matchId: "match-1" });
});

test("parses inbound command frames and fans them to handlers", () => {
  const { transport, sockets } = makeTransport();
  const received: LobbyCommand[] = [];
  transport.onCommand((c) => received.push(c));
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.fireMessage({ type: "subscribed", matchId: "match-1" });

  const command = { commandId: "c1", matchId: "match-1", hostEpoch: 0, kind: "abort" };
  sock.fireMessage({ type: "command", command });
  assert.equal(received.length, 1);
  assert.equal(received[0]?.commandId, "c1");
});

test("malformed / unknown frames are skipped, never fatal", () => {
  const warnings: string[] = [];
  const { transport, sockets } = makeTransport({ onWarn: (e) => warnings.push(e) });
  const received: LobbyCommand[] = [];
  transport.onCommand((c) => received.push(c));
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.fireRaw("not json {{{");
  sock.fireMessage({ type: "totally-unknown" });
  sock.fireMessage(42);
  assert.equal(received.length, 0);
  assert.ok(warnings.includes("bad_frame"));
});

test("ack/heartbeat/report serialise to the wire frames when subscribed", async () => {
  const { transport, sockets } = makeTransport();
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.fireMessage({ type: "subscribed" });

  await transport.sendAck({ commandId: "c1", matchId: "match-1", hostEpoch: 0, ok: true });
  await transport.sendHeartbeat({
    matchId: "match-1",
    hostEpoch: 0,
    phase: "Lobby",
    isHost: true,
    sentAtMs: 1,
  });
  await transport.sendReport({
    matchId: "match-1",
    hostEpoch: 0,
    report: { source: "eogStatsBlock" } as never,
  });

  // [0] is the subscribe frame; the next three are ack/heartbeat/report.
  const types = sock.sent.map((s) => JSON.parse(s).type);
  assert.deepEqual(types, ["subscribe", "ack", "heartbeat", "report"]);
});

test("queues acks while disconnected and flushes them in order on re-subscribe", async () => {
  const { transport, sockets } = makeTransport();
  transport.connect();
  // Not subscribed yet -> these queue.
  await transport.sendAck({ commandId: "a1", matchId: "match-1", hostEpoch: 0, ok: true });
  await transport.sendAck({ commandId: "a2", matchId: "match-1", hostEpoch: 0, ok: false });
  const sock = sockets[0]!;
  sock.fireOpen(); // sends subscribe, still not subscribed
  assert.deepEqual(
    sock.sent.map((s) => JSON.parse(s).type),
    ["subscribe"],
  );
  sock.fireMessage({ type: "subscribed" }); // now flush
  const acks = sock.sent
    .map((s) => JSON.parse(s))
    .filter((f) => f.type === "ack")
    .map((f) => f.ack.commandId);
  assert.deepEqual(acks, ["a1", "a2"]);
});

test("heartbeats are last-write-wins while disconnected (only the freshest flushes)", async () => {
  const { transport, sockets } = makeTransport();
  transport.connect();
  await transport.sendHeartbeat({ matchId: "match-1", hostEpoch: 0, phase: "Lobby", isHost: true, sentAtMs: 1 });
  await transport.sendHeartbeat({ matchId: "match-1", hostEpoch: 0, phase: "InProgress", isHost: true, sentAtMs: 2 });
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.fireMessage({ type: "subscribed" });
  const beats = sock.sent.map((s) => JSON.parse(s)).filter((f) => f.type === "heartbeat");
  assert.equal(beats.length, 1);
  assert.equal(beats[0].heartbeat.sentAtMs, 2);
});

test("a live scoreboard goes out only while subscribed — never queued across a disconnect", async () => {
  const { transport, sockets } = makeTransport();
  transport.connect();
  const live = { matchId: "match-1", snapshot: { gameTime: 60, gameMode: "CLASSIC", players: [] } };
  await transport.sendLiveSnapshot(live); // not subscribed: dropped
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.fireMessage({ type: "subscribed" });
  assert.deepEqual(sock.sent.map((s) => JSON.parse(s).type), ["subscribe"]);
  await transport.sendLiveSnapshot(live);
  const frames = sock.sent.map((s) => JSON.parse(s)).filter((f) => f.type === "live");
  assert.equal(frames.length, 1);
  assert.equal(frames[0].live.snapshot.gameTime, 60);
});

test("reconnects on drop and re-subscribes", () => {
  const { transport, sockets, fire, hasPending } = makeTransport();
  transport.connect();
  const first = sockets[0]!;
  first.fireOpen();
  first.fireMessage({ type: "subscribed" });
  assert.equal(transport.isSubscribed, true);

  // Drop (not a user close, not auth) -> reconnect scheduled.
  first.close(1006);
  assert.equal(transport.isSubscribed, false);
  assert.equal(hasPending(), true);

  fire(); // run the reconnect timer
  assert.equal(sockets.length, 2);
  const second = sockets[1]!;
  second.fireOpen();
  assert.deepEqual(JSON.parse(second.sent[0]!), { type: "subscribe", matchId: "match-1" });
});

test("auth-rejection close (4401) does NOT reconnect", () => {
  const warnings: string[] = [];
  const { transport, sockets, hasPending } = makeTransport({ onWarn: (e) => warnings.push(e) });
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  sock.close(WS_CLOSE_AUTH);
  assert.equal(hasPending(), false);
  assert.ok(warnings.includes("auth_rejected"));
  assert.equal(sockets.length, 1);
});

test("user close() stops the reconnect loop", () => {
  const { transport, sockets, hasPending } = makeTransport();
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  transport.close();
  assert.equal(hasPending(), false);
  // A subsequent transport-driven close event must not schedule a reconnect.
  sock.close(1006);
  assert.equal(hasPending(), false);
});

test("subscribe frame carries the match's guildId for per-match WS authorization", () => {
  const { transport, sockets } = makeTransport({ guildId: "guild-xyz" });
  transport.connect();
  const sock = sockets[0]!;
  sock.fireOpen();
  assert.deepEqual(JSON.parse(sock.sent[0]!), {
    type: "subscribe",
    matchId: "match-1",
    guildId: "guild-xyz",
  });
});

test("4401 invokes onAuthRejected and does NOT auto-reconnect; reconnect() reopens", () => {
  let rejected = 0;
  let token = "stale-jwt";
  const { transport, sockets, hasPending } = makeTransport({
    onAuthRejected: () => {
      rejected += 1;
    },
    tokenProvider: () => token,
  });
  transport.connect();
  const first = sockets[0]!;
  assert.match(first.url, /token=stale-jwt/);
  first.fireOpen();
  first.close(WS_CLOSE_AUTH);

  assert.equal(rejected, 1);
  assert.equal(hasPending(), false); // no auto-reconnect on auth failure
  assert.equal(sockets.length, 1);

  // Simulate the app refreshing the token then reconnecting.
  token = "fresh-jwt";
  transport.reconnect();
  assert.equal(sockets.length, 2);
  const second = sockets[1]!;
  // The reconnect picks up the rotated token via the provider.
  assert.match(second.url, /token=fresh-jwt/);
  second.fireOpen();
  assert.deepEqual(JSON.parse(second.sent[0]!), { type: "subscribe", matchId: "match-1" });
});
