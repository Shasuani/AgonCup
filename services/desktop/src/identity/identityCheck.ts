/**
 * Advisory LCU summoner-identity validation (PLAN.md §3.6 / §2 / §3.6 anti-smurf
 * row in §11).
 *
 * On startup the desktop client reads the locally signed-in account from the LCU
 * (`GET /lol-summoner/v1/current-summoner`, READ-only) and compares its PUUID to
 * the backend's VERIFIED-account PUUID for this user. The result is purely
 * ADVISORY:
 *
 *   - "you appear to be signed into a different Riot account" is a SOFT WARNING
 *     surfaced to the user; it NEVER blocks auth, orchestration, or counts as an
 *     anti-smurf authority (PLAN.md §3.6: "Load-bearing for match execution;
 *     advisory (never authoritative) for result truth, identity, anti-smurf").
 *
 * The canonical identity anchor is the backend's Account-V1-resolved, verified
 * PUUID. The LCU PUUID is only a secondary signal that can catch the common
 * honest mistake of being logged into the wrong client. A `mismatch` is wired to
 * a non-blocking warning; an `unknown` (LCU unreachable / no verified PUUID yet)
 * is silent.
 *
 * `@lol/lcu` is imported as TYPES ONLY so this stays unit-testable under plain
 * `node --test` with a fake adapter (no real LCU).
 */

import type { CurrentSummoner, LcuAdapter, Puuid } from "@lol/lcu";

/** Outcome of the advisory identity comparison. */
export type IdentityVerdict = "match" | "mismatch" | "unknown";

/** Structured advisory result; `verdict` is the only load-bearing field. */
export interface IdentityCheckResult {
  readonly verdict: IdentityVerdict;
  /** The LCU-reported local PUUID, when it could be read (advisory). */
  readonly lcuPuuid?: Puuid;
  /** The backend's verified PUUID we compared against, when known. */
  readonly verifiedPuuid?: Puuid;
  /** Human-readable reason (for the warning surface / logs). */
  readonly detail: string;
}

export interface IdentityCheckInput {
  /** The single audited LCU adapter (READ-only `getCurrentSummoner`). */
  readonly adapter: Pick<LcuAdapter, "getCurrentSummoner">;
  /**
   * The backend's verified PUUID for the signed-in user, or undefined when the
   * account is not yet verified (Account-V1 link pending). Undefined => unknown.
   */
  readonly verifiedPuuid: Puuid | undefined;
}

/**
 * Compare the local LCU account PUUID to the backend's verified PUUID.
 *
 * Returns:
 *   - `match`    — PUUIDs are equal (the happy path);
 *   - `mismatch` — both PUUIDs are known and differ (surface a SOFT warning);
 *   - `unknown`  — the LCU is unreachable / shape-drifted, OR there is no
 *                  verified PUUID yet. Never an error: this is best-effort and
 *                  must never throw into the startup path.
 *
 * NEVER blocks: callers use the verdict only to render an advisory warning.
 */
export async function checkLcuIdentity(
  input: IdentityCheckInput,
): Promise<IdentityCheckResult> {
  if (input.verifiedPuuid === undefined) {
    return {
      verdict: "unknown",
      detail: "no verified PUUID for this user yet (Account-V1 link pending)",
    };
  }

  let summoner: CurrentSummoner;
  try {
    summoner = await input.adapter.getCurrentSummoner();
  } catch (err) {
    // LCU unreachable / not running / RE drift — advisory, so degrade to unknown.
    return {
      verdict: "unknown",
      verifiedPuuid: input.verifiedPuuid,
      detail: `LCU current-summoner unavailable: ${errorMessage(err)}`,
    };
  }

  if (summoner.puuid === input.verifiedPuuid) {
    return {
      verdict: "match",
      lcuPuuid: summoner.puuid,
      verifiedPuuid: input.verifiedPuuid,
      detail: "local LCU account matches the verified account",
    };
  }

  return {
    verdict: "mismatch",
    lcuPuuid: summoner.puuid,
    verifiedPuuid: input.verifiedPuuid,
    detail:
      "local LCU account PUUID does not match the verified account — " +
      "you may be signed into a different Riot account (advisory only)",
  };
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
