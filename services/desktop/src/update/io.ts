/**
 * The three pieces of real IO the macOS swap installer needs, kept out of the
 * pure modules so they stay unit-testable: a streaming download with progress,
 * a streaming SHA-512, and "run this command and wait". Node-only (no Electron).
 */

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

/** GET `url` into `dest`, reporting 0..100 (from Content-Length when known). */
export async function downloadToFile(
  url: string,
  dest: string,
  onProgress: (percent: number) => void,
  doFetch: typeof fetch = fetch,
): Promise<void> {
  const res = await doFetch(url);
  if (!res.ok || res.body === null) {
    throw new Error(`Download failed (HTTP ${res.status}).`);
  }
  const total = Number(res.headers.get("content-length") ?? 0);
  let seen = 0;
  const counter = new Transform({
    transform(chunk: Buffer, _enc, cb) {
      seen += chunk.length;
      if (total > 0) onProgress((seen / total) * 100);
      cb(null, chunk);
    },
  });
  await pipeline(
    Readable.fromWeb(res.body as import("node:stream/web").ReadableStream),
    counter,
    createWriteStream(dest),
  );
  onProgress(100);
}

/** Base64 SHA-512 of a file (electron-builder's feed uses base64 digests). */
export async function sha512Base64(file: string): Promise<string> {
  const hash = createHash("sha512");
  await pipeline(createReadStream(file), hash);
  return hash.digest("base64");
}

/** Run `cmd args…` and resolve on exit code 0. */
export function run(cmd: string, args: readonly string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, [...args], { stdio: "ignore" });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${String(code)}`));
    });
  });
}
