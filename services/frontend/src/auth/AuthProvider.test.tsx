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
