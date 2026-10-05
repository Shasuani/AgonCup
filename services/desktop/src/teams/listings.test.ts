/**
 * Unit tests for the team-listings client (node:test; stubbed fetch, no real
 * network): URLs, methods, bodies, the Bearer + X-Guild-Id headers, the
 * snake_case -> camelCase mapping, error detail, and unreachable mapping.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { createListing, deleteListing, listListings, requestToJoin } from "./listings.js";

type Captured = { url: string; method: string | undefined; body: unknown; headers: Record<string, string> };

function spy(status: number, json: unknown, sink: Captured[]): typeof fetch {
  return (async (url: string, init?: RequestInit) => {
    sink.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(init.body as string) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    });
    return new Response(status === 204 ? null : JSON.stringify(json), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

const LISTING = {
  id: "l1",
  team_id: "t1",
  guild_id: "gA",
  team_name: "Blue Wolves",
  server_name: "Server A",
  region: "euw1",
  message: "need adc",
  created_at: "2026-10-05T10:00:00Z",
  member_count: 3,
  max_members: 5,
  members: [{ display_name: "Cap", role: "captain", rank_text: "Gold II, 45 LP" }],
  viewer_status: null,
  can_remove: false,
};

test("listListings: GETs with Bearer, narrows by region, maps every field", async () => {
  const calls: Captured[] = [];
  const result = await listListings(
    { accessToken: "jwt", region: "euw1" },
    { fetch: spy(200, [LISTING], calls), backendUrl: "http://x" },
  );

  assert.equal(calls[0]?.url, "http://x/api/v1/team-listings?region=euw1");
  assert.equal(calls[0]?.method, "GET");
  assert.equal(calls[0]?.headers["Authorization"], "Bearer jwt");
  assert.equal(calls[0]?.headers["X-Guild-Id"], undefined);
  assert.equal(result.ok, true);
  if (result.ok) {
    const l = result.listings[0]!;
    assert.equal(l.teamName, "Blue Wolves");
    assert.equal(l.serverName, "Server A");
    assert.equal(l.memberCount, 3);
    assert.equal(l.maxMembers, 5);
    assert.equal(l.members[0]?.rankText, "Gold II, 45 LP");
    assert.equal(l.viewerStatus, null);
    assert.equal(l.canRemove, false);
  }
});

test("listListings: no region → no query string", async () => {
  const calls: Captured[] = [];
  await listListings({ accessToken: "j" }, { fetch: spy(200, [], calls), backendUrl: "http://x" });
  assert.equal(calls[0]?.url, "http://x/api/v1/team-listings");
});

test("createListing: POSTs the team with its server as X-Guild-Id", async () => {
  const calls: Captured[] = [];
  const result = await createListing(
    { accessToken: "j", teamId: "t1", guildId: "gA", region: "euw1", message: "  need adc " },
    { fetch: spy(201, { id: "l9" }, calls), backendUrl: "http://x" },
  );

  assert.equal(calls[0]?.url, "http://x/api/v1/team-listings");
  assert.equal(calls[0]?.method, "POST");
  assert.equal(calls[0]?.headers["X-Guild-Id"], "gA");
  assert.deepEqual(calls[0]?.body, { team_id: "t1", region: "euw1", message: "need adc" });
  assert.deepEqual(result, { ok: true, listingId: "l9" });
});

test("createListing: a blank message is not sent", async () => {
  const calls: Captured[] = [];
  await createListing(
    { accessToken: "j", teamId: "t1", region: "euw1", message: "   " },
    { fetch: spy(201, { id: "l9" }, calls), backendUrl: "http://x" },
  );
  assert.deepEqual(calls[0]?.body, { team_id: "t1", region: "euw1" });
});

test("createListing: 409 surfaces the backend's reason", async () => {
  const result = await createListing(
    { accessToken: "j", teamId: "t1", region: "euw1" },
    { fetch: spy(409, { detail: "your team already has a post" }, []), backendUrl: "http://x" },
  );
  assert.deepEqual(result, { ok: false, status: 409, detail: "your team already has a post" });
});

test("deleteListing: DELETEs the listing; 204 is ok", async () => {
  const calls: Captured[] = [];
  const result = await deleteListing(
    { accessToken: "j", listingId: "l1" },
    { fetch: spy(204, null, calls), backendUrl: "http://x" },
  );
  assert.equal(calls[0]?.url, "http://x/api/v1/team-listings/l1");
  assert.equal(calls[0]?.method, "DELETE");
  assert.deepEqual(result, { ok: true });
});

test("requestToJoin: POSTs to the listing and maps the pending request", async () => {
  const calls: Captured[] = [];
  const result = await requestToJoin(
    { accessToken: "j", listingId: "l1" },
    {
      fetch: spy(200, { team_id: "t1", team_name: "Blue Wolves", guild_id: "gA", status: "pending" }, calls),
      backendUrl: "http://x",
    },
  );
  assert.equal(calls[0]?.url, "http://x/api/v1/team-listings/l1/request");
  assert.equal(calls[0]?.method, "POST");
  assert.deepEqual(result, { ok: true, teamId: "t1", teamName: "Blue Wolves", guildId: "gA", requestStatus: "pending" });
});

test("requestToJoin: unreachable backend → status 0, never throws", async () => {
  const boom = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const result = await requestToJoin({ accessToken: "j", listingId: "l1" }, { fetch: boom, backendUrl: "http://x" });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.status, 0);
    assert.match(result.detail, /unreachable/);
  }
});
