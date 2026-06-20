import { describe, expect, it } from "vitest";
import { parseCallbackFragment } from "./AuthCallback";

describe("parseCallbackFragment", () => {
  it("extracts both tokens from the redirect fragment", () => {
    const parsed = parseCallbackFragment("#access_token=abc&refresh_token=def");
    expect(parsed).toEqual({ accessToken: "abc", refreshToken: "def" });
  });

  it("tolerates a missing leading hash", () => {
    expect(parseCallbackFragment("access_token=a&refresh_token=b")).toEqual({
      accessToken: "a",
      refreshToken: "b",
    });
  });

  it("returns null when a token is absent", () => {
    expect(parseCallbackFragment("#access_token=abc")).toBeNull();
    expect(parseCallbackFragment("")).toBeNull();
  });
});
