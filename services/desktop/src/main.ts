/**
 * LeagueOfLeagues desktop client — Electron main process (PLAN.md §3.3 / §3.6).
 *
 * The desktop client is **load-bearing for match EXECUTION** but **advisory for
 * result TRUTH** (PLAN.md §0 split trust model). It:
 *   - autonomously drives the local League client over the LCU (host: create
 *     lobby, invite by PUUID; guest: auto-accept) via the single audited
 *     `@lol/lcu` adapter — the one fix-point for reverse-engineered, drifting
 *     LCU calls;
 *   - captures live state (`:2999`) and end-of-game stats, and reports them to
 *     the backend, where they are corroborated across all 10 clients.
 *
 * ToS posture (PLAN.md §3.3 / §9): every LCU **write** is gated behind a
 * per-session, default-OFF opt-in + presence gate + human-paced rate limits +
 * a recorded consent + a global kill-switch. Korea is excluded. No in-match
 * gameplay automation, ever.
 *
 * Phase-0 status: skeleton wiring only. The app runs natively (it must reach
 * 127.0.0.1 LCU / :2999); it is built and signed/notarized per-OS in CI — the
 * `Dockerfile` here is a build/packaging container, not a runtime.
 */

import { app, BrowserWindow } from "electron";
import {
  LcuAdapterSkeleton,
  type LcuAdapter,
  type LcuAdapterConfig,
  type LcuSchemaVersion,
} from "@lol/lcu";

/** Build the LCU adapter with writes disabled by default (opt-in per session). */
function createLcuAdapter(): LcuAdapter {
  const config: LcuAdapterConfig = {
    // Writes are OFF until the player explicitly opts in this session, and stay
    // off while the kill-switch is engaged or the player is AFK (PLAN.md §3.3).
    writeAutomationOptIn: false,
    killSwitchEngaged: false,
    requirePresenceForWrites: true,
    pacing: {
      minWriteDelayMs: 750,
      writeJitterMs: 500,
      maxWritesPerMinute: 20,
      maxBackoffMs: 30_000,
      maxRetryAttempts: 3,
    },
    // Pinned, fixture-validated client patch (set by the build; placeholder here).
    schemaVersion: "0.0.0-phase0" as LcuSchemaVersion,
  };
  return new LcuAdapterSkeleton(config);
}

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1024,
    height: 720,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  // Phase 0: a placeholder local page is loaded in a later phase.
  return win;
}

app.whenReady().then(() => {
  // Constructed here so the adapter's lifecycle follows the app's.
  const _lcu = createLcuAdapter();
  void _lcu; // wired to the orchestration WS plane in Phase 4.

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

export { createLcuAdapter };
