import { describe, expect, it } from "vitest";
import { parseCallbackFragment } from "./AuthCallback";

// Only the access token rides the fragment now — the refresh token arrives as an
// HttpOnly cookie on the same response, so it never appears in a URL.
describe("parseCallbackFragment", () => {
  it("extracts the access token from the redirect fragment", () => {
    expect(parseCallbackFragment("#access_token=abc")).toEqual({ accessToken: "abc" });
  });

  it("tolerates a missing leading hash", () => {
    expect(parseCallbackFragment("access_token=a")).toEqual({ accessToken: "a" });
  });

  it("ignores anything else in the fragment", () => {
    expect(parseCallbackFragment("#access_token=abc&token_type=bearer")).toEqual({
      accessToken: "abc",
    });
  });

  it("returns null when the access token is absent", () => {
    expect(parseCallbackFragment("#token_type=bearer")).toBeNull();
    expect(parseCallbackFragment("")).toBeNull();
  });
});
