// Vitest setup: register jest-dom matchers (toBeInTheDocument, etc.) and tidy
// the DOM between tests. Imported via vite.config.ts `test.setupFiles`.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
