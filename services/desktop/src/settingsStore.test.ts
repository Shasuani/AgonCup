/**
 * Unit tests for the plain-JSON device settings store (node:test; in-memory fs,
 * no Electron / disk). Covers the save->load round-trip, defaults on a missing
 * or corrupt file, merge-on-save, and the best-effort write failure path.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { SettingsStore, type SettingsStoreFs } from "./settingsStore.js";

/** In-memory filesystem seam. */
function memFs(seed?: Record<string, Buffer>): SettingsStoreFs & { files: Map<string, Buffer> } {
  const files = new Map<string, Buffer>(Object.entries(seed ?? {}));
  return {
    files,
    async readFile(path) {
      const f = files.get(path);
      if (f === undefined) {
        const err = new Error("ENOENT") as Error & { code: string };
        err.code = "ENOENT";
        throw err;
      }
      return f;
    },
    async writeFile(path, data) {
      files.set(path, data);
    },
    async mkdir() {
      return undefined;
    },
  };
}

const FILE = "/userData/settings.json";
const DIR = "/userData";

test("round-trip: a saved automation answer is loaded back", async () => {
  const fs = memFs();
  const store = new SettingsStore({ fs, filePath: FILE, dirPath: DIR });

  assert.equal(await store.save({ automationOptIn: true }), true);
  assert.deepEqual(await store.load(), { automationOptIn: true });

  // Turning it OFF is persisted just as durably (not merely "absent").
  await store.save({ automationOptIn: false });
  assert.deepEqual(await store.load(), { automationOptIn: false });
});

test("missing file loads as defaults (no answer yet)", async () => {
  const store = new SettingsStore({ fs: memFs(), filePath: FILE, dirPath: DIR });
  assert.deepEqual(await store.load(), {});
});

test("corrupt or foreign JSON loads as defaults, never throws", async () => {
  const cases = [Buffer.from("not json"), Buffer.from('"str"'), Buffer.from('{"automationOptIn":"yes"}')];
  for (const contents of cases) {
    const store = new SettingsStore({
      fs: memFs({ [FILE]: contents }),
      filePath: FILE,
      dirPath: DIR,
    });
    assert.deepEqual(await store.load(), {});
  }
});

test("save merges over what is stored instead of clobbering the file", async () => {
  const fs = memFs({ [FILE]: Buffer.from('{"automationOptIn":true,"futureField":1}') });
  const store = new SettingsStore({ fs, filePath: FILE, dirPath: DIR });

  await store.save({});
  // The known field survives a no-op patch (unknown fields are dropped by the
  // defensive parse — the file is untrusted JSON, not a schema contract).
  assert.deepEqual(await store.load(), { automationOptIn: true });
});

test("a failed write resolves false rather than throwing", async () => {
  const fs = memFs();
  fs.writeFile = async () => {
    throw new Error("disk full");
  };
  const store = new SettingsStore({ fs, filePath: FILE, dirPath: DIR });
  assert.equal(await store.save({ automationOptIn: true }), false);
});
