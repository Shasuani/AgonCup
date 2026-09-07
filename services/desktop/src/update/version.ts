/**
 * Version ordering for the client's self-update (mirrors
 * `services/backend/src/app/desktop/service.py` so both sides agree on what
 * "newer" means): a numeric dotted core, an optional pre-release tag that ranks
 * BELOW the plain release of the same core, build metadata ignored, and
 * anything unparseable ranking below every real version.
 */

export interface ParsedVersion {
  readonly core: readonly number[];
  readonly pre: readonly string[] | null;
}

const VERSION_RE = /^\s*v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$/;

export function parseVersion(text: string | null | undefined): ParsedVersion | null {
  if (text === null || text === undefined || text === "") return null;
  const m = VERSION_RE.exec(text);
  if (m === null) return null;
  const core = m[1]!.split(".").map((x) => Number(x));
  const pre = m[2] !== undefined ? m[2].split(".") : null;
  return { core, pre };
}

function comparePre(a: readonly string[], b: readonly string[]): -1 | 0 | 1 {
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i];
    const y = b[i];
    if (x === undefined) return -1; // shorter pre-release ranks lower ("rc" < "rc.1")
    if (y === undefined) return 1;
    const xn = /^\d+$/.test(x);
    const yn = /^\d+$/.test(y);
    if (xn && yn) {
      const d = Number(x) - Number(y);
      if (d !== 0) return d < 0 ? -1 : 1;
    } else if (xn !== yn) {
      return xn ? -1 : 1; // numeric identifiers rank below alphanumeric ones
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return 0;
}

/** -1 / 0 / 1: how `a` ranks against `b`. */
export function compareVersions(a: string, b: string): -1 | 0 | 1 {
  const va = parseVersion(a);
  const vb = parseVersion(b);
  if (va === null && vb === null) return 0;
  if (va === null) return -1;
  if (vb === null) return 1;
  const n = Math.max(va.core.length, vb.core.length);
  for (let i = 0; i < n; i++) {
    const d = (va.core[i] ?? 0) - (vb.core[i] ?? 0);
    if (d !== 0) return d < 0 ? -1 : 1;
  }
  if (va.pre === null && vb.pre === null) return 0;
  if (va.pre === null) return 1;
  if (vb.pre === null) return -1;
  return comparePre(va.pre, vb.pre);
}

/** True when `candidate` is a real version strictly above `current`. */
export function isNewer(
  candidate: string | null | undefined,
  current: string | null | undefined,
): boolean {
  if (parseVersion(candidate) === null) return false;
  if (parseVersion(current) === null) return true;
  return compareVersions(candidate as string, current as string) > 0;
}
