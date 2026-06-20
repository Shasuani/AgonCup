import { defineConfig } from 'vitest/config';

/**
 * Node-environment unit tests. No jsdom needed — this client is
 * framework-agnostic and the tests mock the injected `fetch`. Importing the
 * module performs no network IO.
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
