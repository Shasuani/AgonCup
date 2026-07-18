import { describe, expect, it } from "vitest";
import { decodeAccessToken, isTokenExpired } from "./jwt";

/** Build an unsigned JWT-shaped string with the given payload. */
function makeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "none", typ: "JWT" })}.${b64(payload)}.sig`;
}

describe("decodeAccessToken", () => {
  it("decodes roles + normalizes the backend `gid` claim to guild_id", () => {
    // The backend mints the acting-guild claim as `gid` (not `guild_id`).
    const token = makeJwt({ sub: "u1", gid: "g1", roles: ["guild_admin"], exp: 9999999999 });
    const claims = decodeAccessToken(token);
    expect(claims?.sub).toBe("u1");
    expect(claims?.guild_id).toBe("g1");
    expect(claims?.roles).toEqual(["guild_admin"]);
  });

  it("returns null for malformed tokens", () => {
    expect(decodeAccessToken("not-a-jwt")).toBeNull();
    expect(decodeAccessToken("")).toBeNull();
    expect(decodeAccessToken(null)).toBeNull();
  });
});

describe("isTokenExpired", () => {
  it("is true for a past exp", () => {
    const claims = decodeAccessToken(makeJwt({ sub: "u1", exp: 1000 }));
    expect(isTokenExpired(claims, 2000_000)).toBe(true);
  });
  it("is false when exp is in the future or absent", () => {
    expect(isTokenExpired(decodeAccessToken(makeJwt({ sub: "u1", exp: 9999999999 })))).toBe(false);
    expect(isTokenExpired(decodeAccessToken(makeJwt({ sub: "u1" })))).toBe(false);
  });
});
