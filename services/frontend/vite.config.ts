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
  server: { host: true, port: 5173 },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
