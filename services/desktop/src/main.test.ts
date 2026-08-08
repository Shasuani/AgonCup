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
  refreshFailureReason,
  AUTH_RETRY_MS,
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

// --- transient failure vs a dead credential --------------------------------- //
//
// The distinction these tests pin down is the difference between "open the app
// while the server is down" and "go ask the Discord bot for a new pairing code".

test("refreshFailureReason: only a 401 means the credential is dead", () => {
  assert.equal(refreshFailureReason(401), "expired");
  // 0 = never reached the backend; 429/500/502 = the backend declining to answer.
  for (const status of [0, 429, 500, 502, 503]) {
    assert.equal(refreshFailureReason(status), "unavailable");
  }
});

test("decideBoot: unreachable backend -> unavailable, token KEPT (no re-pair)", async () => {
  const store = memBootStore({ refreshToken: "rt-good" });
  const decision = await decideBoot({
    store,
    refreshSession: async () =>
      ({ ok: false, status: 0, detail: "Backend unreachable" }) satisfies RefreshResult,
  });
  assert.equal(decision.kind, "unavailable");
  // The whole point: the credential survives, so the next attempt can use it.
  assert.equal(store.cleared, 0);
  assert.deepEqual(store.current, { refreshToken: "rt-good" });
});

test("decideBoot: a 429 is transient too — the token is not thrown away", async () => {
  const store = memBootStore({ refreshToken: "rt-good" });
  const decision = await decideBoot({
    store,
    refreshSession: async () =>
      ({ ok: false, status: 429, detail: "too many attempts" }) satisfies RefreshResult,
  });
  assert.equal(decision.kind, "unavailable");
  assert.equal(store.cleared, 0);
});

test("decideBoot: unavailable carries the cached user (for the reconnecting view)", async () => {
  const user = { userId: "u-1", discordUserId: "d-1", displayName: "Kostas" };
  const decision = await decideBoot({
    store: memBootStore({ refreshToken: "rt-good", user }),
    refreshSession: async () => ({ ok: false, status: 0, detail: "down" }) satisfies RefreshResult,
  });
  assert.equal(decision.kind, "unavailable");
  if (decision.kind === "unavailable") {
    assert.deepEqual(decision.user, user);
  }
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
  assert.deepEqual(await session.refresh(), { ok: true });
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
  assert.deepEqual(await session.refresh(), { ok: true });
  // The user survives rotation (the refresh response carries none).
  assert.deepEqual(session.user, user);
  assert.deepEqual(saved, [{ refreshToken: "rt-2", user }]);
});

test("DesktopSession.refresh: a 401 reports 'expired', keeps old token, no persist", async () => {
  const saved: PersistedSession[] = [];
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async (s) => void saved.push(s), clear: async () => {} },
    refreshSession: async () => ({ ok: false, status: 401, detail: "revoked" }) satisfies RefreshResult,
  });
  assert.deepEqual(await session.refresh(), { ok: false, reason: "expired" });
  assert.equal(session.accessToken, "at-1");
  assert.deepEqual(saved, []);
});

test("DesktopSession.refresh: an unreachable backend is 'unavailable', not 'expired'", async () => {
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async () => {}, clear: async () => {} },
    refreshSession: async () =>
      ({ ok: false, status: 0, detail: "Backend unreachable" }) satisfies RefreshResult,
  });
  assert.deepEqual(await session.refresh(), { ok: false, reason: "unavailable" });
  // The token is untouched, so the caller can keep the session and try later.
  assert.equal(session.accessToken, "at-1");
});

test("DesktopSession.refresh: concurrent callers share ONE rotation", async () => {
  let calls = 0;
  let release: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: { save: async () => {}, clear: async () => {} },
    refreshSession: async () => {
      calls += 1;
      await gate;
      return { ok: true, accessToken: "at-2", refreshToken: "rt-2" } satisfies RefreshResult;
    },
  });

  // Three screens' worth of IPC calls hitting an expired access token at once.
  const all = Promise.all([session.refresh(), session.refresh(), session.refresh()]);
  release!();
  assert.deepEqual(await all, [{ ok: true }, { ok: true }, { ok: true }]);
  // Not an optimisation: a second request presenting the token the first already
  // spent is indistinguishable from a replay, and the backend burns the family.
  assert.equal(calls, 1);

  // The gate is released, so a LATER refresh is a fresh rotation, not the cached one.
  assert.deepEqual(await session.refresh(), { ok: true });
  assert.equal(calls, 2);
});

test("DesktopSession.refresh: a store that cannot persist does not kill the session", async () => {
  const warnings: string[] = [];
  const session = new DesktopSession({
    accessToken: "at-1",
    refreshToken: "rt-1",
    store: {
      save: async () => {
        throw new Error("OS encryption unavailable");
      },
      clear: async () => {},
    },
    refreshSession: async () =>
      ({ ok: true, accessToken: "at-2", refreshToken: "rt-2" }) satisfies RefreshResult,
    onWarn: (event) => void warnings.push(event),
  });
  // The rotation SUCCEEDED; only the next boot is affected. Throwing here would
  // reject the IPC call and discard a session that works right now.
  assert.deepEqual(await session.refresh(), { ok: true });
  assert.equal(session.accessToken, "at-2");
  assert.deepEqual(warnings, ["secure_store_unavailable"]);
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

test("WS 4401 with an UNREACHABLE backend keeps the session (no auth-lost)", async () => {
  const sockets: DriveSocket[] = [];
  let authLost = 0;
  const warnings: string[] = [];
  // Every scheduled tick, WITH its interval: the orchestration controller's
  // heartbeat shares this scheduler, so the retry has to be picked out by its
  // own interval. An array (not a `let`) because control-flow analysis cannot
  // see an assignment made inside a callback and narrows such a variable to
  // `null` forever.
  const scheduled: { ms: number; fn: () => void }[] = [];
  const rt = new DesktopRuntime({
    adapter: fakeAdapter(),
    config: { realtimeUrl: "wss://h/ws", token: "jwt", tokenProvider: () => "jwt", verifiedPuuid: undefined },
    wsFactory: (url) => {
      const s = new DriveSocket(url);
      sockets.push(s);
      return s;
    },
    scheduler: { every: (ms, fn) => (scheduled.push({ ms, fn }), () => {}) },
    clock: CLOCK,
    refreshAuth: async () => ({ ok: false, reason: "unavailable" as const }),
    onAuthLost: () => {
      authLost += 1;
    },
    onWarn: (event) => void warnings.push(event),
  });
  rt.onMatchAssigned("m1");
  const first = sockets[0]!;
  first.fireOpen();
  first.close(WS_CLOSE_AUTH);
  await flush();
  // A backend restart mid-tournament used to dump the user on the pairing screen.
  assert.equal(authLost, 0);
  assert.ok(warnings.includes("auth_refresh_unavailable"));

  // The transport does NOT auto-reconnect after a 4401, so the session would be
  // alive but permanently mute if nothing re-opened it. The runtime schedules
  // that retry itself.
  assert.equal(sockets.length, 1, "no reconnect before the retry fires");
  const retries = scheduled.filter((s) => s.ms === AUTH_RETRY_MS);
  assert.equal(retries.length, 1, "exactly one retry must be pending");
  retries[0]!.fn();
  assert.equal(sockets.length, 2, "the retry re-opens the socket");
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
    refreshAuth: async () => ({ ok: false, reason: "expired" as const }),
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
