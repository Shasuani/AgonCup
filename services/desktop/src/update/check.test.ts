/** `GET /desktop/version` client (node:test; stubbed fetch). */

import assert from "node:assert/strict";
import { test } from "node:test";

import { checkDesktopVersion } from "./check.js";

test("checkDesktopVersion: URL, query, mapping", async () => {
  let captured: string | undefined;
  const spyFetch: typeof fetch = (async (url: string) => {
    captured = url;
    return new Response(
      JSON.stringify({
        platform: "darwin",
        current: "0.1.0",
        latest: "0.2.0",
        minimum: null,
        status: "update_available",
        up_to_date: false,
        required: false,
        published_at: "2026-09-08T10:00:00.000Z",
        download_url: "https://x/downloads/LeagueOfLeagues.dmg",
        package_url: "https://x/downloads/LeagueOfLeagues-0.2.0-universal.zip",
        package_sha512: "abc==",
        package_size: 42,
        feed_url: "https://x/downloads",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;

  const r = await checkDesktopVersion(
    { currentVersion: "0.1.0", platform: "darwin" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );
  assert.equal(captured, "http://x/api/v1/desktop/version?platform=darwin&version=0.1.0");
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.release.status, "update_available");
    assert.equal(r.release.latest, "0.2.0");
    assert.equal(r.release.upToDate, false);
    assert.equal(r.release.packageUrl, "https://x/downloads/LeagueOfLeagues-0.2.0-universal.zip");
    assert.equal(r.release.packageSha512, "abc==");
    assert.equal(r.release.packageSize, 42);
    assert.equal(r.release.publishedAt, "2026-09-08T10:00:00.000Z");
  }
});

test("checkDesktopVersion: non-mac platforms ask for the Windows feed; odd bodies collapse to unknown", async () => {
  let captured: string | undefined;
  const spyFetch: typeof fetch = (async (url: string) => {
    captured = url;
    return new Response(JSON.stringify({ status: "weird", download_url: "" }), { status: 200 });
  }) as unknown as typeof fetch;
  const r = await checkDesktopVersion(
    { currentVersion: "0.1.0", platform: "linux" },
    { fetch: spyFetch, backendUrl: "http://x" },
  );
  assert.ok(captured?.includes("platform=win32"));
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.release.status, "unknown");
    assert.equal(r.release.upToDate, true);
    assert.equal(r.release.latest, null);
  }
});

test("checkDesktopVersion: HTTP error surfaces detail; unreachable maps to status 0", async () => {
  const err: typeof fetch = (async () =>
    new Response(JSON.stringify({ detail: "rate_limited" }), { status: 429 })) as unknown as typeof fetch;
  const r = await checkDesktopVersion({ currentVersion: "0.1.0", platform: "win32" }, { fetch: err, backendUrl: "http://x" });
  assert.deepEqual(r, { ok: false, status: 429, detail: "rate_limited" });

  const down: typeof fetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  const r2 = await checkDesktopVersion({ currentVersion: "0.1.0", platform: "win32" }, { fetch: down, backendUrl: "http://x" });
  assert.equal(r2.ok, false);
  if (!r2.ok) assert.equal(r2.status, 0);
});
