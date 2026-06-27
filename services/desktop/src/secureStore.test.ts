/**
 * Unit tests for the encrypted-at-rest session store (node:test; mock
 * safeStorage + in-memory fs, no Electron / disk). Covers the encrypt->persist->
 * decrypt round-trip, missing-file / decrypt-failure -> null, the refusal to
 * persist plaintext when OS encryption is unavailable, and clear().
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { SecureStore, type SafeStorageLike, type SecureStoreFs } from "./secureStore.js";

/** A reversible "encryption" (prefix tag) so the round-trip is observable. */
function fakeSafeStorage(available = true): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plain) => Buffer.from("ENC:" + plain, "utf8"),
    decryptString: (buf) => {
      const s = buf.toString("utf8");
      if (!s.startsWith("ENC:")) throw new Error("bad ciphertext");
      return s.slice(4);
    },
  };
}

/** In-memory filesystem seam. */
function memFs(seed?: Record<string, Buffer>): SecureStoreFs & { files: Map<string, Buffer> } {
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
    async rm(path) {
      files.delete(path);
    },
  };
}

const FILE = "/userData/session.bin";
const DIR = "/userData";

test("round-trip: save encrypts to the file and load decrypts it back", async () => {
  const fs = memFs();
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs, filePath: FILE, dirPath: DIR });

  await store.save({ refreshToken: "rt-123" });

  // Persisted bytes are CIPHERTEXT, never the plaintext token.
  const onDisk = fs.files.get(FILE)!.toString("utf8");
  assert.match(onDisk, /^ENC:/);
  assert.equal(onDisk.includes("rt-123") && !onDisk.startsWith("ENC:"), false);

  const loaded = await store.load();
  // No user was saved -> load normalizes the absent user to null.
  assert.deepEqual(loaded, { refreshToken: "rt-123", user: null });
});

test("round-trip: the cached paired-user survives save -> load", async () => {
  const fs = memFs();
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs, filePath: FILE, dirPath: DIR });
  const user = { userId: "u-1", discordUserId: "d-1", displayName: "Kostas" };

  await store.save({ refreshToken: "rt-123", user });

  const loaded = await store.load();
  assert.deepEqual(loaded, { refreshToken: "rt-123", user });
});

test("load: malformed cached user is normalized to null (refresh token still loads)", async () => {
  const fs = memFs({ [FILE]: Buffer.from('ENC:{"refreshToken":"rt-123","user":"garbage"}', "utf8") });
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs, filePath: FILE, dirPath: DIR });
  assert.deepEqual(await store.load(), { refreshToken: "rt-123", user: null });
});

test("load: missing file returns null (no stored session)", async () => {
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs: memFs(), filePath: FILE, dirPath: DIR });
  assert.equal(await store.load(), null);
});

test("load: undecryptable blob (tampered / wrong machine) returns null", async () => {
  const fs = memFs({ [FILE]: Buffer.from("not-our-ciphertext", "utf8") });
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs, filePath: FILE, dirPath: DIR });
  assert.equal(await store.load(), null);
});

test("save: refuses to persist when OS encryption is unavailable (never plaintext)", async () => {
  const fs = memFs();
  const store = new SecureStore({
    safeStorage: fakeSafeStorage(false),
    fs,
    filePath: FILE,
    dirPath: DIR,
  });
  await assert.rejects(() => store.save({ refreshToken: "rt-123" }), /unavailable/);
  assert.equal(fs.files.has(FILE), false);
});

test("load: returns null when encryption is unavailable", async () => {
  const fs = memFs({ [FILE]: Buffer.from("ENC:{\"refreshToken\":\"x\"}", "utf8") });
  const store = new SecureStore({
    safeStorage: fakeSafeStorage(false),
    fs,
    filePath: FILE,
    dirPath: DIR,
  });
  assert.equal(await store.load(), null);
});

test("clear: removes the persisted session and is safe when absent", async () => {
  const fs = memFs();
  const store = new SecureStore({ safeStorage: fakeSafeStorage(), fs, filePath: FILE, dirPath: DIR });
  await store.save({ refreshToken: "rt-123" });
  assert.equal(fs.files.has(FILE), true);
  await store.clear();
  assert.equal(fs.files.has(FILE), false);
  // Second clear is a no-op, never throws.
  await store.clear();
});
