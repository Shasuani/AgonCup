// Test-only helper: build an unsigned JWT-shaped string + an AuthSession from a
// claims object. The client-side gate never verifies the signature, so an
// unsigned token is sufficient to exercise role decoding in tests.

import type { AuthSession } from "../auth/AuthProvider";

function b64url(obj: unknown): string {
  return btoa(JSON.stringify(obj))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function makeTestJwt(claims: Record<string, unknown>): string {
  return `${b64url({ alg: "none", typ: "JWT" })}.${b64url(claims)}.sig`;
}

export function makeSession(claims: Record<string, unknown>): AuthSession {
  return {
    accessToken: makeTestJwt({ exp: 9_999_999_999, ...claims }),
    refreshToken: "refresh-token",
  };
}
