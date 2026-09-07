/**
 * Unit tests for the boot-time server URL resolution (node:test, no Electron).
 * The one property that matters: an installed build with NO env must land on
 * production, never on localhost.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { DEV_ORIGIN, PRODUCTION_ORIGIN, resolveServerUrls } from "./serverUrls.js";

test("an installed build with no env talks to production everywhere", () => {
  const urls = resolveServerUrls({ isPackaged: true, env: {} });
  assert.equal(urls.backendUrl, PRODUCTION_ORIGIN);
  assert.equal(urls.realtimeUrl, "wss://lol.gameras.gr/api/v1/realtime/ws");
  assert.equal(urls.downloadsUrl, "https://lol.gameras.gr/downloads");
});

test("a dev run with no env keeps the localhost defaults", () => {
  const urls = resolveServerUrls({ isPackaged: false, env: {} });
  assert.equal(urls.backendUrl, DEV_ORIGIN);
  assert.equal(urls.realtimeUrl, "ws://localhost:8000/api/v1/realtime/ws");
  assert.equal(urls.downloadsUrl, "http://localhost:8000/downloads");
});

test("BACKEND_URL wins even when packaged, and the others follow it", () => {
  const urls = resolveServerUrls({
    isPackaged: true,
    env: { BACKEND_URL: "https://staging.example.test/" },
  });
  assert.equal(urls.backendUrl, "https://staging.example.test");
  assert.equal(urls.realtimeUrl, "wss://staging.example.test/api/v1/realtime/ws");
  assert.equal(urls.downloadsUrl, "https://staging.example.test/downloads");
});

test("an explicit REALTIME_URL is taken verbatim", () => {
  const urls = resolveServerUrls({
    isPackaged: false,
    env: { REALTIME_URL: "ws://127.0.0.1:9999/rt" },
  });
  assert.equal(urls.backendUrl, DEV_ORIGIN);
  assert.equal(urls.realtimeUrl, "ws://127.0.0.1:9999/rt");
});

test("empty env values count as unset", () => {
  const urls = resolveServerUrls({ isPackaged: true, env: { BACKEND_URL: "", REALTIME_URL: "" } });
  assert.equal(urls.backendUrl, PRODUCTION_ORIGIN);
  assert.equal(urls.realtimeUrl, "wss://lol.gameras.gr/api/v1/realtime/ws");
});
