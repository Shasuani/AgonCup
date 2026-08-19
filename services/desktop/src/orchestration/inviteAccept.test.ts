/**
 * Guest-side auto-accept against the REAL `@lol/lcu` adapter (node:test; stub
 * LcuRuntime, no League client / network).
 *
 * Pins the regression that let every invite sit un-accepted: the LCU's
 * received-invitation record identifies the inviter by the LCU-local 36-char
 * `fromPuuid` / numeric `fromSummonerId` — never by the canonical 78-char PUUID
 * the backend fences on — so a filter made only of the canonical PUUID matched
 * nothing. Acceptance must work through the advisory LCU identifiers, and the
 * fence must stay closed for any other inviter.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  HttpRequest,
  HttpResponse,
  LcuAdapterConfig,
  LcuRuntime,
  LcuSchemaVersion,
  Puuid,
  SummonerId,
} from "@lol/lcu";
import { LcuAdapterImpl, inviterMatchesFilter } from "@lol/lcu";

/** A valid lockfile line so the adapter can derive auth + base URL. */
const LOCKFILE_CONTENTS = "LeagueClient:12345:54321:secretpw:https";

/** The backend's canonical 78-char fence — NOT what the LCU ever reports. */
const CANONICAL_PUUID = "canonical-puuid-host" as Puuid;
/** What the guest's LCU actually reports for the same host. */
const HOST_LCU_PUUID = "11111111-2222-3333-4444-555555555555" as Puuid;
const HOST_SUMMONER_ID = "777" as SummonerId;
const EPOCH = 0 as never;

function adapterWith(args: {
  respond: (req: HttpRequest) => HttpResponse;
  requests?: HttpRequest[];
}): LcuAdapterImpl {
  const runtime: LcuRuntime = {
    http: {
      async request(req: HttpRequest): Promise<HttpResponse> {
        args.requests?.push(req);
        return args.respond(req);
      },
    },
    fs: {
      readLockfile: async () => ({ contents: LOCKFILE_CONTENTS, mtimeMs: 1 }),
    },
    clock: { nowMs: () => 0 },
    sleeper: { sleep: async () => {} },
    random: { next: () => 0 },
  };
  const config: LcuAdapterConfig = {
    writeAutomationOptIn: true, // the accept POST is a gated write
    killSwitchEngaged: false,
    requirePresenceForWrites: true,
    pacing: {
      minWriteDelayMs: 0,
      writeJitterMs: 0,
      maxWritesPerMinute: 20,
      maxBackoffMs: 0,
      maxRetryAttempts: 1,
    },
    schemaVersion: "0.0.0-test" as LcuSchemaVersion,
  };
  return new LcuAdapterImpl({ config, runtime });
}

test("regression: the canonical fence alone matches nothing the LCU reports", () => {
  // This is exactly the filter the client used to arm — and why nobody accepted.
  const matched = inviterMatchesFilter(
    { invitationId: "inv-1", fromPuuid: HOST_LCU_PUUID, invitationType: "lobby" },
    { expectedInviterPuuid: CANONICAL_PUUID, hostEpoch: EPOCH },
  );
  assert.equal(matched, false);
});

test("the host's LCU-local puuid satisfies the fence", () => {
  const matched = inviterMatchesFilter(
    { invitationId: "inv-1", fromPuuid: HOST_LCU_PUUID, invitationType: "lobby" },
    {
      expectedInviterPuuid: CANONICAL_PUUID,
      expectedInviterLcuPuuid: HOST_LCU_PUUID,
      hostEpoch: EPOCH,
    },
  );
  assert.equal(matched, true);
});

test("the host's summonerId satisfies the fence when no puuid is readable", () => {
  const matched = inviterMatchesFilter(
    { invitationId: "inv-1", fromSummonerId: HOST_SUMMONER_ID, invitationType: "lobby" },
    {
      expectedInviterPuuid: CANONICAL_PUUID,
      expectedInviterSummonerId: HOST_SUMMONER_ID,
      hostEpoch: EPOCH,
    },
  );
  assert.equal(matched, true);
});

test("another player's invite never satisfies the fence", () => {
  const matched = inviterMatchesFilter(
    {
      invitationId: "inv-1",
      fromPuuid: "99999999-aaaa-bbbb-cccc-dddddddddddd" as Puuid,
      fromSummonerId: "42" as SummonerId,
      invitationType: "lobby",
    },
    {
      expectedInviterPuuid: CANONICAL_PUUID,
      expectedInviterLcuPuuid: HOST_LCU_PUUID,
      expectedInviterSummonerId: HOST_SUMMONER_ID,
      hostEpoch: EPOCH,
    },
  );
  assert.equal(matched, false);
});

test("an absent identity field never matches an absent fence field", () => {
  // Neither side carries a summonerId: undefined === undefined must NOT accept.
  const matched = inviterMatchesFilter(
    { invitationId: "inv-1", fromPuuid: HOST_LCU_PUUID, invitationType: "lobby" },
    {
      expectedInviterPuuid: CANONICAL_PUUID,
      expectedInviterSummonerId: HOST_SUMMONER_ID,
      hostEpoch: EPOCH,
    },
  );
  assert.equal(matched, false);
});

test("getReceivedInvitations keeps summonerId-only records and drops identity-less ones", async () => {
  const adapter = adapterWith({
    respond: (req) => {
      if (/received-invitations$/.test(req.url) && req.method === "GET") {
        return {
          status: 200,
          body: JSON.stringify([
            // Modern record: both identity fields, summonerId as a JSON number.
            {
              invitationId: "inv-both",
              fromPuuid: HOST_LCU_PUUID,
              fromSummonerId: 777,
              invitationType: "lobby",
            },
            // Older schema: no fromPuuid at all — must survive on summonerId.
            { invitationId: "inv-sid", fromSummonerId: 778, invitationType: "lobby" },
            // No readable inviter identity — can never satisfy the fence: drop.
            { invitationId: "inv-blind", invitationType: "lobby" },
          ]),
        };
      }
      return { status: 404, body: "{}" };
    },
  });

  const invitations = await adapter.getReceivedInvitations();

  assert.deepEqual(invitations, [
    {
      invitationId: "inv-both",
      fromPuuid: HOST_LCU_PUUID,
      fromSummonerId: "777",
      invitationType: "lobby",
    },
    { invitationId: "inv-sid", fromSummonerId: "778", invitationType: "lobby" },
  ]);
});

test("the controller accepts the host's real-world invite end to end", async () => {
  const requests: HttpRequest[] = [];
  const adapter = adapterWith({
    requests,
    respond: () => ({ status: 204, body: "" }),
  });
  const controller = adapter.makeInviteAcceptController({
    expectedInviterPuuid: CANONICAL_PUUID,
    expectedInviterLcuPuuid: HOST_LCU_PUUID,
    expectedInviterSummonerId: HOST_SUMMONER_ID,
    hostEpoch: EPOCH,
  });

  const decision = await controller.handle({
    invitationId: "inv-1",
    fromPuuid: HOST_LCU_PUUID,
    fromSummonerId: HOST_SUMMONER_ID,
    invitationType: "lobby",
  });

  assert.equal(decision.accepted, true);
  const accept = requests.find((r) => r.method === "POST");
  assert.ok(accept, "no accept POST was sent");
  assert.match(accept.url, /received-invitations\/inv-1\/accept$/);
});

test("the controller still refuses a stranger's lobby invite", async () => {
  const requests: HttpRequest[] = [];
  const adapter = adapterWith({
    requests,
    respond: () => ({ status: 204, body: "" }),
  });
  const controller = adapter.makeInviteAcceptController({
    expectedInviterPuuid: CANONICAL_PUUID,
    expectedInviterLcuPuuid: HOST_LCU_PUUID,
    expectedInviterSummonerId: HOST_SUMMONER_ID,
    hostEpoch: EPOCH,
  });

  const decision = await controller.handle({
    invitationId: "inv-2",
    fromPuuid: "99999999-aaaa-bbbb-cccc-dddddddddddd" as Puuid,
    fromSummonerId: "42" as SummonerId,
    invitationType: "lobby",
  });

  assert.equal(decision.accepted, false);
  assert.equal(decision.reason, "inviter-mismatch");
  assert.equal(requests.filter((r) => r.method === "POST").length, 0);
});

test("a failed accept POST is not reported as accepted (the arm stays alive)", async () => {
  // Regression: the accept's HTTP status was never checked, so a 404 (invite
  // expired) came back `accepted: true`, which stopped the guest's poll loop
  // and silently disarmed auto-accept for the rest of the match.
  const adapter = adapterWith({
    respond: (req) =>
      req.method === "POST" ? { status: 404, body: "" } : { status: 200, body: "[]" },
  });
  const controller = adapter.makeInviteAcceptController({
    expectedInviterPuuid: CANONICAL_PUUID,
    expectedInviterLcuPuuid: HOST_LCU_PUUID,
    hostEpoch: EPOCH,
  });

  const decision = await controller.handle({
    invitationId: "inv-expired",
    fromPuuid: HOST_LCU_PUUID,
    invitationType: "lobby",
  });

  assert.equal(decision.accepted, false);
  assert.equal(decision.reason, "accept-failed");
});

// ---------------------------------------------------------------------------
// createCustomLobby idempotency probe: reuse OUR lobby, never a foreign one
// ---------------------------------------------------------------------------

const LOBBY_SPEC = {
  matchId: "match-1",
  hostEpoch: EPOCH,
  configuration: {
    gameMode: "CLASSIC",
    mapId: 11,
    mutators: { id: 1 },
    spectatorPolicy: "NotAllowed",
    teamSize: 5,
  },
  lobbyName: "LoL-abc12345",
  lobbyPassword: "pw",
  invitees: [],
} as never;

function lobbyJson(over: Record<string, unknown>): string {
  return JSON.stringify({ partyId: "party-9", gameConfig: over });
}

test("create reuses the current lobby when its name is the match's", async () => {
  const requests: HttpRequest[] = [];
  const adapter = adapterWith({
    requests,
    respond: () => ({
      status: 200,
      body: lobbyJson({ isCustom: true, customLobbyName: "LoL-abc12345" }),
    }),
  });
  const handle = await adapter.createCustomLobby(LOBBY_SPEC);
  assert.equal(handle.lobbyId, "party-9");
  assert.equal(requests.filter((r) => r.method === "POST").length, 0);
});

test("create does NOT adopt a foreign lobby (wrong name, or not custom)", async () => {
  // Regression: "a lobby exists" used to be reuse enough — a normal-queue party
  // or a friend's custom game was returned as the match lobby, and nine
  // tournament players were then invited into it.
  for (const gameConfig of [
    { isCustom: true, customLobbyName: "someone-elses-game" },
    { isCustom: false },
  ]) {
    const requests: HttpRequest[] = [];
    const adapter = adapterWith({
      requests,
      respond: (req) =>
        req.method === "POST"
          ? { status: 200, body: JSON.stringify({ partyId: "party-new" }) }
          : { status: 200, body: lobbyJson(gameConfig) },
    });
    const handle = await adapter.createCustomLobby(LOBBY_SPEC);
    assert.equal(handle.lobbyId, "party-new");
    assert.equal(requests.filter((r) => r.method === "POST").length, 1);
  }
});

test("an unreadable lobby name falls back to isCustom and reuses (drift bias)", async () => {
  // When the RE name field drifts, wrongly re-creating would kick every joined
  // player; wrongly reusing is repaired by the invite fence. Bias to reuse.
  const requests: HttpRequest[] = [];
  const adapter = adapterWith({
    requests,
    respond: () => ({ status: 200, body: lobbyJson({ isCustom: true }) }),
  });
  const handle = await adapter.createCustomLobby(LOBBY_SPEC);
  assert.equal(handle.lobbyId, "party-9");
  assert.equal(requests.filter((r) => r.method === "POST").length, 0);
});
