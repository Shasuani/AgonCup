/**
 * Unit tests for the Electron main wiring (node:test; no real Electron/WS/LCU).
 *
 * The load-bearing property: importing `main.ts` performs NO IO — no socket, no
 * Electron window — so it is safe under `node --test`. Also exercises the
 * DesktopRuntime lifecycle (match-assigned -> controller created, match-ended ->
 * torn down) and the advisory identity check, all with injected fakes.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DesktopRuntime,
  DesktopSession,
  createLcuAdapter,
  decideBoot,
  type BootStore,
} from "./main.js";
import type { WebSocketLike } from "./transport/wsTransport.js";
import { WS_OPEN, WS_CLOSE_AUTH, WS_CLOSED } from "./transport/wsTransport.js";
import type { Clock, Scheduler } from "./orchestration/index.js";
import type { PersistedSession } from "./secureStore.js";
import type { RefreshResult } from "./auth/pairClient.js";
import type { CurrentSummoner, LcuAdapter, Puuid } from "@lol/lcu";

// Importing main.ts above must not have opened anything; reaching here is the
// import-safety assertion (the test process has no electron/ws sockets live).
test("importing main.ts is IO-free (no Electron window, no socket)", () => {
  // createLcuAdapter is a pure constructor — calling it opens nothing.
  const adapter = createLcuAdapter();
  assert.equal(typeof adapter.getCurrentSummoner, "function");
});

/** A no-op fake socket so DesktopRuntime.onMatchAssigned never touches `ws`. */
class FakeSocket implements WebSocketLike {
  readyState = WS_OPEN;
  send(): void {}
  close(): void {}
  on(): void {}
}

function fakeAdapter(over: Partial<{ summoner: CurrentSummoner; throws: boolean }> = {}): LcuAdapter {
  return {
    async getCurrentSummoner() {
      if (over.throws) throw new Error("no LCU");
      return (
        over.summoner ??
        ({
          puuid: "puuid-A",
          summonerId: "1",
          displayName: "x",
          gameName: "Chama",
          tagLine: "Shasu",
        } as CurrentSummoner)
      );
    },
  } as unknown as LcuAdapter;
}

const SCHEDULER: Scheduler = { every: () => () => {} };
const CLOCK: Clock = { nowMs: () => 0 };

function makeRuntime(over: { verifiedPuuid?: Puuid; adapter?: LcuAdapter; onWarn?: (e: string, d: Record<string, unknown>) => void } = {}) {
  return new DesktopRuntime({
    adapter: over.adapter ?? fakeAdapter(),
    config: {
      realtimeUrl: "wss://host/api/v1/realtime/ws",
      token: "jwt",
      verifiedPuuid: over.verifiedPuuid,
    },
    wsFactory: () => new FakeSocket(),
    scheduler: SCHEDULER,
    clock: CLOCK,
    ...(over.onWarn ? { onWarn: over.onWarn } : {}),
  });
}

test("constructing DesktopRuntime opens nothing; no live matches", () => {
  const rt = makeRuntime();
  assert.equal(rt.liveMatchCount, 0);
});

test("onMatchAssigned creates a controller; onMatchEnded tears it down (idempotent)", () => {
  const rt = makeRuntime();
  rt.onMatchAssigned("m1");
  rt.onMatchAssigned("m1"); // idempotent
  assert.equal(rt.liveMatchCount, 1);
  rt.onMatchAssigned("m2");
  assert.equal(rt.liveMatchCount, 2);
  rt.onMatchEnded("m1");
  assert.equal(rt.liveMatchCount, 1);
  rt.onMatchEnded("missing"); // no-op
  rt.shutdown();
  assert.equal(rt.liveMatchCount, 0);
});

test("validateIdentity: match verdict, no warning", async () => {
  const warnings: string[] = [];
  const rt = makeRuntime({
    verifiedPuuid: "puuid-A" as Puuid,
    onWarn: (e) => warnings.push(e),
  });
  const res = await rt.validateIdentity();
  assert.equal(res.verdict, "match");
  assert.equal(warnings.includes("identity_mismatch"), false);
});

test("validateIdentity: mismatch surfaces an advisory warning, never throws", async () => {
  const warnings: string[] = [];
  const rt = makeRuntime({
    verifiedPuuid: "puuid-Z" as Puuid,
    adapter: fakeAdapter({
      summoner: {
        puuid: "puuid-A",
        summonerId: "1",
        displayName: "x",
        gameName: "Chama",
        tagLine: "Shasu",
      } as CurrentSummoner,
    }),
    onWarn: (e) => warnings.push(e),
  });
  const res = await rt.validateIdentity();
  assert.equal(res.verdict, "mismatch");
  assert.ok(warnings.includes("identity_mismatch"));
});

// --- boot decision (replaces the dev-login stub) ---------------------------- //

/** In-memory boot store recording saves/clears for assertions. */
function memBootStore(initial: PersistedSession | null): BootStore & {
  readonly saved: PersistedSession[];
  cleared: number;
  current: PersistedSession | null;
} {
  return {
    saved: [] as PersistedSession[],
    cleared: 0,
    current: initial,
    async load() {
      return this.current;
    },
    async save(s) {
      this.current = s;
      this.saved.push(s);
    },
    async clear() {
      this.cleared += 1;
      this.current = null;
    },
  };
}

test("decideBoot: no stored token -> needs-pairing (no refresh attempted)", async () => {
  const store = memBootStore(null);
  let refreshCalls = 0;
  const decision = await decideBoot({
    store,
    refreshSession: async () => {
      refreshCalls += 1;
      return { ok: true, accessToken: "a", refreshToken: "r" } satisfies RefreshResult;
    },
  });
  assert.equal(decision.kind, "needs-pairing");
  assert.equal(refreshCalls, 0);
});

test("decideBoot: stored token -> silent refresh -> signed-in, rotated token persisted", async () => {
  const store = memBootStore({ refreshToken: "rt-old" });
  const decision = await decideBoot({
    store,
    refreshSession: async (rt) => {
      assert.equal(rt, "rt-old");
      return { ok: true, accessToken: "at-new", refreshToken: "rt-new" } satisfies RefreshResult;
    },
  });
  assert.equal(decision.kind, "signed-in");
  if (decision.kind === "signed-in") {
    assert.equal(decision.accessToken, "at-new");
    assert.equal(decision.refreshToken, "rt-new");
    assert.equal(decision.user, null);
  }
  // The ROTATED refresh token was persisted (the predecessor is now invalid).
  assert.deepEqual(store.saved, [{ refreshToken: "rt-new", user: null }]);
});

test("decideBoot: cached user is carried into the decision AND re-persisted on rotation", async () => {
  const user = { userId: "u-1", discordUserId: "d-1", displayName: "Kostas" };
  const store = memBootStore({ refreshToken: "rt-old", user });
  const decision = await decideBoot({
    store,
    refreshSession: async () =>
      ({ ok: true, accessToken: "at-new", refreshToken: "rt-new" }) satisfies RefreshResult,
  });
  assert.equal(decision.kind, "signed-in");
  if (decision.kind === "signed-in") {
    // /refresh echoes no user, so without the cache this would be null and the
    // paired name would be blank until a re-pair.
    assert.deepEqual(decision.user, user);
  }
  // The cached user is re-persisted alongside the rotated token (not dropped).
  assert.deepEqual(store.saved, [{ refreshToken: "rt-new", user }]);
});

test("decideBoot: failed refresh (expired/revoked) -> clears + needs-pairing", async () => {
  const store = memBootStore({ refreshToken: "rt-stale" });
  const decision = await decideBoot({
    store,
    refreshSession: async () => ({ ok: false, status: 401, detail: "revoked" }) satisfies RefreshResult,
  });
  assert.equal(decision.kind, "needs-pairing");
  assert.equal(store.cleared, 1);
  assert.equal(store.current, null);
});

test("DesktopSession.refresh: rotates tokens and persists the rotated refresh token", async () => {
  const saved: PersistedSession[] = [];
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async (s) => void saved.push(s), clear: async () => {} },
    refreshSession: async (rt) => {
      assert.equal(rt, "rt-1");
      return { ok: true, accessToken: "at-2", refreshToken: "rt-2" } satisfies RefreshResult;
    },
  });
  assert.equal(session.accessToken, "at-1");
  const ok = await session.refresh();
  assert.equal(ok, true);
  assert.equal(session.accessToken, "at-2");
  assert.deepEqual(saved, [{ refreshToken: "rt-2", user: null }]);
});

test("DesktopSession.refresh: re-persists the cached user alongside the rotated token", async () => {
  const user = { userId: "u-1", discordUserId: "d-1", displayName: "Kostas" };
  const saved: PersistedSession[] = [];
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async (s) => void saved.push(s), clear: async () => {} },
    refreshSession: async () =>
      ({ ok: true, accessToken: "at-2", refreshToken: "rt-2" }) satisfies RefreshResult,
    user,
  });
  assert.deepEqual(session.user, user);
  assert.equal(await session.refresh(), true);
  // The user survives rotation (the refresh response carries none).
  assert.deepEqual(session.user, user);
  assert.deepEqual(saved, [{ refreshToken: "rt-2", user }]);
});

test("DesktopSession.refresh: failed refresh returns false, keeps old token, no persist", async () => {
  const saved: PersistedSession[] = [];
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async (s) => void saved.push(s), clear: async () => {} },
    refreshSession: async () => ({ ok: false, status: 401, detail: "revoked" }) satisfies RefreshResult,
  });
  assert.equal(await session.refresh(), false);
  assert.equal(session.accessToken, "at-1");
  assert.deepEqual(saved, []);
});

// --- per-match guildId threading + silent-refresh on WS 4401 ---------------- //

/** A controllable fake socket so the test can drive open/close + read sends. */
class DriveSocket implements WebSocketLike {
  readyState: number = WS_OPEN;
  readonly url: string;
  readonly sent: string[] = [];
  #listeners: Record<string, ((...a: never[]) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(code?: number): void {
    this.readyState = WS_CLOSED;
    (this.#listeners["close"] ?? []).forEach((l) => (l as (c: number) => void)(code ?? 1000));
  }
  on(event: string, listener: (...a: never[]) => void): void {
    (this.#listeners[event] ??= []).push(listener);
  }
  fireOpen(): void {
    (this.#listeners["open"] ?? []).forEach((l) => (l as () => void)());
  }
  fireMessage(obj: unknown): void {
    (this.#listeners["message"] ?? []).forEach((l) => (l as (d: unknown) => void)(JSON.stringify(obj)));
  }
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

test("onMatchAssigned threads the match's guildId into the subscribe frame", () => {
  const sockets: DriveSocket[] = [];
  const rt = new DesktopRuntime({
    adapter: fakeAdapter(),
    config: { realtimeUrl: "wss://h/ws", token: "jwt", verifiedPuuid: undefined },
    wsFactory: (url) => {
      const s = new DriveSocket(url);
      sockets.push(s);
      return s;
    },
    scheduler: SCHEDULER,
    clock: CLOCK,
  });
  rt.onMatchAssigned("m1", "guild-7");
  sockets[0]!.fireOpen();
  assert.deepEqual(JSON.parse(sockets[0]!.sent[0]!), {
    type: "subscribe",
    matchId: "m1",
    guildId: "guild-7",
  });
});

test("WS 4401 silently refreshes once and reconnects with the rotated token", async () => {
  const sockets: DriveSocket[] = [];
  let token = "stale-jwt";
  let refreshCalls = 0;
  let authLost = 0;
  const rt = new DesktopRuntime({
    adapter: fakeAdapter(),
    config: {
      realtimeUrl: "wss://h/ws",
      token,
      tokenProvider: () => token,
      verifiedPuuid: undefined,
    },
    wsFactory: (url) => {
      const s = new DriveSocket(url);
      sockets.push(s);
      return s;
    },
    scheduler: SCHEDULER,
    clock: CLOCK,
    refreshAuth: async () => {
      refreshCalls += 1;
      token = "fresh-jwt";
      return { ok: true, token };
    },
    onAuthLost: () => {
      authLost += 1;
    },
  });

  rt.onMatchAssigned("m1", "guild-7");
  const first = sockets[0]!;
  assert.match(first.url, /token=stale-jwt/);
  first.fireOpen();
  first.fireMessage({ type: "subscribed" });

  // Backend rejects auth -> transport stops, runtime refreshes + reconnects.
  first.close(WS_CLOSE_AUTH);
  await flush();

  assert.equal(refreshCalls, 1);
  assert.equal(authLost, 0);
  assert.equal(sockets.length, 2);
  // The reconnect uses the rotated token (via the shared provider).
  assert.match(sockets[1]!.url, /token=fresh-jwt/);
});

test("WS 4401 with a failing refresh surfaces auth-lost (re-pair)", async () => {
  const sockets: DriveSocket[] = [];
  let authLost = 0;
  const rt = new DesktopRuntime({
    adapter: fakeAdapter(),
    config: { realtimeUrl: "wss://h/ws", token: "jwt", tokenProvider: () => "jwt", verifiedPuuid: undefined },
    wsFactory: (url) => {
      const s = new DriveSocket(url);
      sockets.push(s);
      return s;
    },
    scheduler: SCHEDULER,
    clock: CLOCK,
    refreshAuth: async () => ({ ok: false }),
    onAuthLost: () => {
      authLost += 1;
    },
  });
  rt.onMatchAssigned("m1");
  const first = sockets[0]!;
  first.fireOpen();
  first.close(WS_CLOSE_AUTH);
  await flush();
  assert.equal(authLost, 1);
  assert.equal(sockets.length, 1); // no reconnect when refresh fails
});
