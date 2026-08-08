import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { AuthProvider, useAuth } from "./AuthProvider";
import { makeSession } from "../test/jwt";

function Probe(): JSX.Element {
  const { isAuthenticated, claims, getToken, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="auth">{String(isAuthenticated)}</span>
      <span data-testid="roles">{(claims?.roles ?? []).join(",")}</span>
      <span data-testid="token">{getToken() ?? "none"}</span>
      <button onClick={login}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe("AuthProvider", () => {
  it("exposes decoded claims + token for an initial session", () => {
    const session = makeSession({ sub: "u1", roles: ["guild_admin"], guild_id: "g1" });
    render(
      <AuthProvider initialSession={session}>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("auth")).toHaveTextContent("true");
    expect(screen.getByTestId("roles")).toHaveTextContent("guild_admin");
    expect(screen.getByTestId("token")).toHaveTextContent(session.accessToken);
  });

  it("starts unauthenticated with no session", () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("auth")).toHaveTextContent("false");
    expect(screen.getByTestId("token")).toHaveTextContent("none");
  });

  it("login() redirects to the backend auth start URL", () => {
    const redirect = vi.fn();
    render(
      <AuthProvider redirect={redirect}>
        <Probe />
      </AuthProvider>,
    );
    act(() => {
      screen.getByText("login").click();
    });
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(redirect.mock.calls[0]?.[0]).toContain("/api/v1/auth/login");
  });

  it("logout() clears the session", () => {
    render(
      <AuthProvider initialSession={makeSession({ sub: "u1", roles: ["guild_admin"] })}>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("auth")).toHaveTextContent("true");
    act(() => {
      screen.getByText("logout").click();
    });
    expect(screen.getByTestId("auth")).toHaveTextContent("false");
  });

  it("useAuth throws outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/AuthProvider/);
    spy.mockRestore();
  });
});

describe("AuthProvider session restore (HttpOnly cookie)", () => {
  function ProbeWithRestoring(): JSX.Element {
    const { isAuthenticated, isRestoring, getToken } = useAuth();
    return (
      <div>
        <span data-testid="auth">{String(isAuthenticated)}</span>
        <span data-testid="restoring">{String(isRestoring)}</span>
        <span data-testid="token">{getToken() ?? "none"}</span>
      </div>
    );
  }

  it("restores a session on mount from the cookie, so a reload stays signed in", async () => {
    // The reported bug: pressing refresh sent the user back through Discord,
    // because nothing was persisted and nothing was restored.
    const session = makeSession({ sub: "u1", roles: ["guild_admin"] });
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          access_token: session.accessToken,
          refresh_token: "ignored",
          token_type: "bearer",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <ProbeWithRestoring />
      </AuthProvider>,
    );

    await screen.findByText("true", { selector: '[data-testid="auth"]' });
    expect(screen.getByTestId("token")).toHaveTextContent(session.accessToken);
    // The browser attaches the cookie itself; the body carries no token.
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/v1/auth/refresh");
    expect(init?.credentials).toBe("same-origin");
    vi.unstubAllGlobals();
  });

  it("issues ONE refresh even when the effect is double-invoked (StrictMode)", async () => {
    // /auth/refresh rotates, and re-presenting a rotated token reads as theft and
    // burns the family. StrictMode double-mounts in dev, so an unguarded boot
    // refresh fired twice with the same cookie and revoked the session it had
    // just restored — every reload bounced the user back through Discord.
    const session = makeSession({ sub: "u1", roles: ["guild_admin"] });
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: session.accessToken,
            refresh_token: "ignored",
            token_type: "bearer",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrictMode>
        <AuthProvider>
          <ProbeWithRestoring />
        </AuthProvider>
      </StrictMode>,
    );

    await screen.findByText("true", { selector: '[data-testid="auth"]' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("lands on signed-out when the cookie is missing or dead", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 })),
    );

    render(
      <AuthProvider>
        <ProbeWithRestoring />
      </AuthProvider>,
    );

    await screen.findByText("false", { selector: '[data-testid="restoring"]' });
    expect(screen.getByTestId("auth")).toHaveTextContent("false");
    vi.unstubAllGlobals();
  });

  it("revokes server-side on logout instead of only clearing React state", async () => {
    // Clearing state alone left the refresh token valid in the database — a real
    // hole once the cookie makes sessions outlive the tab.
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) => new Response("{}", { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider initialSession={makeSession({ sub: "u1" })}>
        <Probe />
      </AuthProvider>,
    );
    await act(async () => {
      screen.getByText("logout").click();
    });

    expect(screen.getByTestId("auth")).toHaveTextContent("false");
    const logoutCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/auth/logout"),
    );
    expect(logoutCall).toBeDefined();
    expect(logoutCall![1]?.method).toBe("POST");
    expect(logoutCall![1]?.credentials).toBe("same-origin");
    vi.unstubAllGlobals();
  });
});
