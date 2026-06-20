// Minimal, dependency-free JWT payload decoder for the CLIENT-SIDE role gate.
//
// SECURITY: This does NOT verify the signature. It only reads the claims so the
// UI can decide what to show. The backend independently verifies the token and
// enforces authorization on every request — the gate here is purely UX.

import type { AccessTokenClaims } from "@lol/clients-ts";

/** base64url -> JSON-decoded object, or null when the input is unparseable. */
function decodeBase64Url(segment: string): unknown {
  try {
    const padded = segment
      .replace(/-/g, "+")
      .replace(/_/g, "/")
      .padEnd(segment.length + ((4 - (segment.length % 4)) % 4), "=");
    const binary = atob(padded);
    // Decode UTF-8 bytes (atob yields a binary string).
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    const json = new TextDecoder().decode(bytes);
    return JSON.parse(json) as unknown;
  } catch {
    return null;
  }
}

/**
 * Decode the claims from an access JWT without verifying it. Returns null when
 * the token is malformed or its payload is not an object.
 */
export function decodeAccessToken(token: string | null | undefined): AccessTokenClaims | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const payload = parts[1];
  if (!payload) return null;
  const claims = decodeBase64Url(payload);
  if (claims === null || typeof claims !== "object") return null;
  return claims as AccessTokenClaims;
}

/** True when the token's `exp` (epoch seconds) is in the past. */
export function isTokenExpired(claims: AccessTokenClaims | null, nowMs: number = Date.now()): boolean {
  if (!claims || typeof claims.exp !== "number") return false;
  return claims.exp * 1000 <= nowMs;
}
