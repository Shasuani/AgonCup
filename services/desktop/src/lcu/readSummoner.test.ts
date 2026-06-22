/**
 * Unit tests for the READ-ONLY LCU current-summoner read (node:test; stub
 * LcuRuntime, no real League client / network). Covers successful parsing and
 * the league-not-running / lcu-error failure mappings.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  HttpRequest,
  HttpResponse,
  LcuRuntime,
  LockfileSnapshot,
} from "@lol/lcu";

import { readCurrentSummoner } from "./readSummoner.js";

/** A valid lockfile line so the adapter can derive auth + base URL. */
const LOCKFILE_CONTENTS = "LeagueClient:12345:54321:secretpw:https";

/** Build a stub runtime whose HTTP transport runs `respond`, sharing a request log. */
function stubRuntime(args: {
  respond: (req: HttpRequest) => HttpResponse | Promise<HttpResponse>;
  lockfile?: () => Promise<LockfileSnapshot>;
  requests?: HttpRequest[];
}): LcuRuntime {
  return {
    http: {
      async request(req: HttpRequest): Promise<HttpResponse> {
        args.requests?.push(req);
        return args.respond(req);
      },
    },
    fs: {
      readLockfile:
        args.lockfile ??
        (async () => ({ contents: LOCKFILE_CONTENTS, mtimeMs: 1 })),
    },
    clock: { nowMs: () => 0 },
    sleeper: { sleep: async () => {} },
    random: { next: () => 0 },
  };
}

test("ok: parses puuid + summonerId + gameName/tagLine + region (region-locale primary)", async () => {
  const requests: HttpRequest[] = [];
  const runtime = stubRuntime({
    requests,
    respond: (req) => {
      if (/current-summoner$/.test(req.url)) {
        return {
          status: 200,
          body: JSON.stringify({
            puuid: "puuid-XYZ",
            summonerId: 777,
            displayName: "",
            gameName: "Chama",
            tagLine: "Shasu",
          }),
        };
      }
      if (/riotclient\/region-locale$/.test(req.url)) {
        // LIVE finding: region comes from here; webRegion "eune" -> "eun1".
        return {
          status: 200,
          body: JSON.stringify({ region: "EUNE", webRegion: "eune", locale: "en_GB" }),
        };
      }
      return { status: 404, body: "{}" };
    },
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.puuid, "puuid-XYZ");
    assert.equal(result.summonerId, "777");
    assert.equal(result.displayName, "");
    assert.equal(result.gameName, "Chama");
    assert.equal(result.tagLine, "Shasu");
    // Region is read from the LCU (region-locale webRegion), never user-chosen.
    assert.equal(result.region, "eun1");
  }
  // READ-ONLY: current-summoner GET + the region-locale GET for the region.
  assert.equal(requests.length, 2);
  assert.equal(requests[0]?.method, "GET");
  assert.match(requests[0]?.url ?? "", /current-summoner$/);
  assert.match(requests[1]?.url ?? "", /riotclient\/region-locale$/);
});

test("ok: region falls back to session.platformId when region-locale is unreadable", async () => {
  const requests: HttpRequest[] = [];
  const runtime = stubRuntime({
    requests,
    respond: (req) => {
      if (/current-summoner$/.test(req.url)) {
        return {
          status: 200,
          body: JSON.stringify({
            puuid: "puuid-XYZ",
            summonerId: 777,
            gameName: "Chama",
            tagLine: "Shasu",
          }),
        };
      }
      if (/lol-login\/v1\/session$/.test(req.url)) {
        return { status: 200, body: JSON.stringify({ platformId: "EUW1" }) };
      }
      // region-locale 404s -> fall back to the login session.
      return { status: 404, body: "{}" };
    },
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.region, "euw1");
  }
});

test("league_not_running: lockfile missing (ENOENT) maps to a friendly reason", async () => {
  const runtime = stubRuntime({
    respond: () => ({ status: 200, body: "{}" }),
    lockfile: async () => {
      const err = new Error("no lockfile") as Error & { code: string };
      err.code = "ENOENT";
      throw err;
    },
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "league_not_running");
  }
});

test("league_not_running: connection refused maps to league_not_running", async () => {
  const runtime = stubRuntime({
    respond: () => {
      const err = new Error("refused") as Error & { code: string };
      err.code = "ECONNREFUSED";
      throw err;
    },
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "league_not_running");
  }
});

test("league_not_running: HTTP 404 (no summoner signed in) maps to not-running", async () => {
  const runtime = stubRuntime({
    respond: () => ({ status: 404, body: "" }),
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "league_not_running");
  }
});

test("lcu_error: HTTP 500 surfaces as an lcu_error", async () => {
  const runtime = stubRuntime({
    respond: () => ({ status: 500, body: "boom" }),
  });

  const result = await readCurrentSummoner(runtime);

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "lcu_error");
  }
});
