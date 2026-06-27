/**
 * Glob-independent test runner for the desktop suite.
 *
 * The previous `node --import tsx --test "src/**\/*.test.ts"` relied on node>=21's
 * INTERNAL glob expansion. Under node 20 (and any node<21) the quoted glob is
 * passed literally; node prints "Could not find .../src/**\/*.test.ts", runs ZERO
 * tests, and EXITS 0 — a silent false-green that masks the entire suite. This
 * runner discovers `*.test.ts` files itself (no shell/node glob dependency) and
 * spawns the node test runner with an explicit file list, so it behaves
 * identically on node 20, 22, and beyond, on every OS.
 */

import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const srcDir = join(here, "..", "src");

/** Recursively collect every `*.test.ts` under `dir`. */
function findTests(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...findTests(full));
    } else if (entry.isFile() && entry.name.endsWith(".test.ts")) {
      out.push(full);
    }
  }
  return out;
}

const files = findTests(srcDir);
if (files.length === 0) {
  console.error(`No *.test.ts files found under ${srcDir}`);
  process.exit(1);
}

const result = spawnSync(
  process.execPath,
  ["--import", "tsx", "--test", ...files],
  { stdio: "inherit" },
);

if (result.error) {
  console.error(result.error);
  process.exit(1);
}
process.exit(result.status ?? 1);
