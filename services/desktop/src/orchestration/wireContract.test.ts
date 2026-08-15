/**
 * The client half of the backend<->desktop wire contract (PLAN.md §3.6.1).
 *
 * Every other test in this folder builds its own commands, so it proves the
 * controller is consistent with *itself*. That is exactly how the contract broke:
 * the backend sent `{"type":"create_lobby_command","match_id":…}`, this side
 * discriminated on `kind` and read `matchId`, and the controller dropped every
 * real command on its first line — no ack, no log — while both suites stayed
 * green.
 *
 * So these tests use no hand-written commands at all. They read the SAME golden
 * file the backend's `test_wire_contract.py` asserts its models serialize to, and
 * push those bytes through the real controller. If the two sides ever disagree
 * again, one of the two tests fails immediately.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { OrchestrationController } from "./controller.js";
import type {
  BackendTransport,
  HostHeartbeat,
  LobbyAck,
  LobbyCommand,
  ResultReportMessage,
  Scheduler,
} from "./protocol.js";

/** The golden bytes, owned by the contract package both sides depend on. */
const GOLDEN = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../../libs/lol-realtime/contract/lobby-commands.golden.json", import.meta.url)),
    "utf8",
  ),
) as Record<string, LobbyCommand>;

/** The match id baked into the fixture — the controller must be bound to it. */
const MATCH = "11111111-2222-3333-4444-555555555555";

function harness() {
  const acks: LobbyAck[] = [];
  const calls: string[] = [];
  const invited: string[] = [];
  let armedInviter: string | null = null;
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
    async sendHeartbeat(_h: HostHeartbeat) {},
    async sendReport(_r: ResultReportMessage) {},
  };
  const scheduler: Scheduler = { every: () => () => {} };
  const adapter = {
    async createCustomLobby(spec: Record<string, unknown>) {
      calls.push("createCustomLobby");
      capturedSpec = spec;
      return { lobbyId: "lobby-xyz" } as never;
    },
    async invite(ids: readonly unknown[]) {
      calls.push("invite");
      invited.push(...(ids as string[]));
    },
    async autoAcceptInvite(filter: { expectedInviterPuuid: string }) {
      calls.push("autoAcceptInvite");
      armedInviter = filter.expectedInviterPuuid;
      return () => {};
    },
    async getGameflowPhase() {
      return "Lobby";
    },
  };
  let capturedSpec: Record<string, unknown> | null = null;

  new OrchestrationController({
    adapter: adapter as never,
    transport,
    scheduler,
    clock: { nowMs: () => 0 },
    config: { matchId: MATCH, heartbeatMs: 2000 },
  }).start();

  return {
    emit: (c: LobbyCommand) => handler?.(c),
    acks,
    calls,
    invited,
    spec: () => capturedSpec,
    armedInviter: () => armedInviter,
  };
}

const settle = async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
};

test("the backend's own create_lobby bytes create the lobby and invite the roster", async () => {
  const h = harness();
  h.emit(GOLDEN["create_lobby_command"]!);
  await settle();

  // The regression in one line: this used to be [] because `matchId` was
  // undefined and the command returned before touching the adapter.
  assert.deepEqual(h.calls, ["createCustomLobby", "invite"]);
  assert.deepEqual(h.invited, ["sid-1", "sid-2"]);
  assert.equal(h.acks[0]?.ok, true);
  assert.equal(h.acks[0]?.lcuLobbyId, "lobby-xyz");
});

test("the wire spec is translated into the shape the LCU adapter takes", async () => {
  const h = harness();
  h.emit(GOLDEN["create_lobby_command"]!);
  await settle();

  // The backend sends the game config flat; the LCU wants it nested, plus the
  // two constants the backend does not carry.
  const spec = h.spec() as Record<string, Record<string, unknown>>;
  assert.deepEqual(spec["configuration"], {
    gameMode: "CLASSIC",
    mapId: 11,
    teamSize: 5,
    mutators: { id: 1 },
    spectatorPolicy: "AllAllowed",
  });
  assert.equal(spec["lobbyName"], "LoL-11111111");
  assert.equal(spec["lobbyPassword"], "111111");
});

test("the backend's auto_accept bytes arm the guest against THAT host", async () => {
  const h = harness();
  h.emit(GOLDEN["auto_accept_command"]!);
  await settle();

  assert.deepEqual(h.calls, ["autoAcceptInvite"]);
  // Fenced to the designated host: arming must never mean "accept anything".
  assert.equal(h.armedInviter(), "puuid-host");
  assert.equal(h.acks[0]?.ok, true);
});

test("the backend's invite_players bytes re-invite the named roster", async () => {
  const h = harness();
  h.emit(GOLDEN["invite_players_command"]!);
  await settle();

  assert.deepEqual(h.calls, ["invite"]);
  assert.deepEqual(h.invited, ["sid-1", "sid-2"]);
  assert.equal(h.acks[0]?.ok, true);
});

test("the backend's abort bytes tear the automation down", async () => {
  const h = harness();
  h.emit(GOLDEN["abort_command"]!);
  await settle();

  assert.deepEqual(h.calls, []);
  assert.equal(h.acks[0]?.ok, true);
});

test("a promotion with no prior spec refuses instead of guessing a lobby", async () => {
  const h = harness();
  h.emit(GOLDEN["promote_host_command"]!);
  await settle();

  // No spec was ever received, and a promotion carries none. Creating a lobby
  // here would invite the wrong people; refusing hands it to the manual fallback.
  assert.deepEqual(h.calls, []);
  assert.equal(h.acks[0]?.ok, false);
});

test("a promotion after a create re-creates the SAME lobby verbatim", async () => {
  const h = harness();
  h.emit(GOLDEN["create_lobby_command"]!);
  await settle();
  h.emit(GOLDEN["promote_host_command"]!);
  await settle();

  // PLAN.md §3.6.2: a successor re-creates verbatim — same name, same invitees.
  assert.deepEqual(h.calls, ["createCustomLobby", "invite", "createCustomLobby", "invite"]);
  assert.deepEqual(h.invited, ["sid-1", "sid-2", "sid-1", "sid-2"]);
});

test("every command in the golden file is one this client handles", async () => {
  // A command the backend can send and this switch has no case for does nothing
  // at all, silently — which is how `invite_players_command` sat unhandled while
  // the backend published it on every single lobby init.
  for (const [name, command] of Object.entries(GOLDEN)) {
    const h = harness();
    h.emit(command);
    await settle();
    assert.equal(h.acks.length, 1, `${name} produced no ack — unhandled command type`);
  }
});
