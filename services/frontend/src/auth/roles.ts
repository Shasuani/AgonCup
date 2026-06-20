// Role constants + helpers shared by the auth context and the RoleGate.
//
// Roles come from the decoded access-token `roles` claim (per-acting-guild on
// the backend). The two privileged roles the admin SPA gates on:
//   - platform_operator: cross-guild operator (lifecycle, federation feed).
//   - guild_admin:        admin within the acting guild (CRUD, disputes).

import type { AccessTokenClaims } from "@lol/clients-ts";

export const PLATFORM_OPERATOR = "platform_operator" as const;
export const GUILD_ADMIN = "guild_admin" as const;

export type AdminRole = typeof PLATFORM_OPERATOR | typeof GUILD_ADMIN;

/** All role strings on the token (defensively normalized to a string[]). */
export function rolesOf(claims: AccessTokenClaims | null): string[] {
  if (!claims || !Array.isArray(claims.roles)) return [];
  return claims.roles.filter((r): r is string => typeof r === "string");
}

/** True when the token carries at least one of the required roles. */
export function hasAnyRole(claims: AccessTokenClaims | null, required: readonly string[]): boolean {
  if (required.length === 0) return true;
  const owned = new Set(rolesOf(claims));
  return required.some((r) => owned.has(r));
}
