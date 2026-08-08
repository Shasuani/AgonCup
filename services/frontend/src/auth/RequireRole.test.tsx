import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./AuthProvider";
import { RequireRole, RoleGate } from "./RequireRole";
import { GUILD_ADMIN } from "./roles";
import { makeSession } from "../test/jwt";

function renderGatedRoute(rolesClaim: string[] | undefined) {
  const session =
    rolesClaim === undefined ? null : makeSession({ sub: "u1", roles: rolesClaim });
  return render(
    <AuthProvider initialSession={session}>
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route element={<RequireRole anyOf={[GUILD_ADMIN]} />}>
            <Route path="/admin" element={<div>ADMIN AREA</div>} />
          </Route>
          <Route path="/not-authorized" element={<div>NOT AUTHORIZED</div>} />
          <Route path="/login" element={<div>LOGIN</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("RequireRole", () => {
  it("lets an admin through", () => {
    renderGatedRoute(["guild_admin"]);
    expect(screen.getByText("ADMIN AREA")).toBeInTheDocument();
  });

  it("redirects a non-admin to NotAuthorized", () => {
    renderGatedRoute(["captain"]);
    expect(screen.queryByText("ADMIN AREA")).not.toBeInTheDocument();
    expect(screen.getByText("NOT AUTHORIZED")).toBeInTheDocument();
  });

  it("redirects an unauthenticated user to login", async () => {
    // With no session the provider first attempts a silent cookie refresh, so
    // the redirect lands only once that resolves (here: a failed fetch).
    renderGatedRoute(undefined);
    expect(await screen.findByText("LOGIN")).toBeInTheDocument();
  });
});

describe("RoleGate", () => {
  it("renders children for a holder and fallback otherwise", () => {
    render(
      <AuthProvider initialSession={makeSession({ sub: "u1", roles: ["guild_admin"] })}>
        <RoleGate anyOf={[GUILD_ADMIN]} fallback={<span>HIDDEN</span>}>
          <span>VISIBLE</span>
        </RoleGate>
      </AuthProvider>,
    );
    expect(screen.getByText("VISIBLE")).toBeInTheDocument();
    expect(screen.queryByText("HIDDEN")).not.toBeInTheDocument();
  });

  it("renders the fallback for a non-holder", () => {
    render(
      <AuthProvider initialSession={makeSession({ sub: "u1", roles: ["player"] })}>
        <RoleGate anyOf={[GUILD_ADMIN]} fallback={<span>HIDDEN</span>}>
          <span>VISIBLE</span>
        </RoleGate>
      </AuthProvider>,
    );
    expect(screen.getByText("HIDDEN")).toBeInTheDocument();
    expect(screen.queryByText("VISIBLE")).not.toBeInTheDocument();
  });
});
