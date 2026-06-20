import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Admin SPA dev/build config (PLAN.md §3.4). The generated API client lives in
// libs/lol-clients-ts (produced by `make gen-clients` from the backend OpenAPI).
export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 5173 },
});
