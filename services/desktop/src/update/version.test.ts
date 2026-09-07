/** Version ordering (node:test) — pinned to the backend's rules. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { compareVersions, isNewer, parseVersion } from "./version.js";

test("parseVersion: v-prefix, pre-release, build metadata, junk", () => {
  assert.deepEqual(parseVersion("v1.2.3-beta.1+build.5"), { core: [1, 2, 3], pre: ["beta", "1"] });
  assert.deepEqual(parseVersion("0.2.0"), { core: [0, 2, 0], pre: null });
  assert.equal(parseVersion(""), null);
  assert.equal(parseVersion("latest"), null);
  assert.equal(parseVersion(undefined), null);
});

test("compareVersions: numeric parts, missing parts, pre-releases, junk", () => {
  const table: Array<[string, string, -1 | 0 | 1]> = [
    ["0.1.0", "0.2.0", -1],
    ["0.2.0", "0.2.0", 0],
    ["0.10.0", "0.9.9", 1],
    ["1.0.0", "1.0", 0],
    ["1.0.0-rc.1", "1.0.0", -1],
    ["1.0.0-rc.2", "1.0.0-rc.10", -1],
    ["1.0.0-rc", "1.0.0-rc.1", -1],
    ["1.0.0-1", "1.0.0-alpha", -1],
    ["junk", "0.0.1", -1],
    ["junk", "junk", 0],
  ];
  for (const [a, b, expected] of table) {
    assert.equal(compareVersions(a, b), expected, `${a} vs ${b}`);
    assert.equal(compareVersions(b, a), (expected === 0 ? 0 : -expected) as -1 | 0 | 1, `${b} vs ${a}`);
  }
});

test("isNewer: unknown current counts as outdated, unknown candidate never does", () => {
  assert.equal(isNewer("0.2.0", "0.1.0"), true);
  assert.equal(isNewer("0.2.0", "0.2.0"), false);
  assert.equal(isNewer("0.2.0", null), true);
  assert.equal(isNewer(null, "0.1.0"), false);
  assert.equal(isNewer("nope", "0.1.0"), false);
});
