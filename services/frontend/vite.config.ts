/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Admin SPA dev/build/test config (PLAN.md §3.4).
//
// The API client lives in libs/lol-clients-ts (hand-written now, later produced
// by `make gen-clients` from the backend OpenAPI). Its package `main`/`types`
// point at `./dist`, which is not built in this workspace, so we alias the bare
// specifier to its TypeScript source. Vite/Vitest compile the source directly,
// and tsconfig `paths` mirrors this for the typechecker.
const clientsSrc = fileURLToPath(
  new URL("../../libs/lol-clients-ts/src/index.ts", import.meta.url),
);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@lol/clients-ts": clientsSrc,
    },
  },
  server: {
    host: true,
    port: 5173,
    // Dev: forward API calls to the backend so the SPA stays same-origin (no
    // CORS) and config.apiBaseUrl can default to "". Override the target with
    // VITE_API_BASE_URL if the backend runs elsewhere.
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL || "http://localhost:8000",
        changeOrigin: true,
        // When the backend isn't listening, http-proxy emits ECONNREFUSED and
        // Vite would otherwise return an opaque empty-body 500. Reply with a
        // clear 502 + JSON so the SPA can say "backend down" instead of guessing.
        configure: (proxy) => {
          proxy.on("error", (err, _req, res) => {
            console.warn(
              `[vite proxy] backend unreachable (${err.message}) — start it: make dev-backend`,
            );
            if ("writeHead" in res && !res.headersSent) {
              res.writeHead(502, { "Content-Type": "application/json" });
            }
            res.end(
              JSON.stringify({
                error: "backend_unreachable",
                detail:
                  "Vite could not reach the backend on :8000. Start it: make dev-backend (or VS Code 'Stack: Backend + Frontend').",
              }),
            );
          });
        },
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
