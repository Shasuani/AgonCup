import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProviders, AppRoutes } from "./App";
import { makeSession } from "./test/jwt";

function renderAt(path: string, opts: { roles?: string[] } = {}) {
  const session = opts.roles ? makeSession({ sub: "u1", roles: opts.roles }) : null;
  return render(
    <AppProviders initialSession={session}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("App route table", () => {
  it("shows the login screen when unauthenticated and visiting a gated route", async () => {
    renderAt("/tournaments");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Sign in with Discord/i })).toBeInTheDocument(),
    );
  });

  it("renders an admin screen for an admin (lazy-loaded)", async () => {
    renderAt("/tournaments", { roles: ["guild_admin"] });
    expect(
      await screen.findByRole("heading", { name: "Tournaments" }),
    ).toBeInTheDocument();
  });

  it("blocks a non-admin from an admin screen", async () => {
    renderAt("/tournaments", { roles: ["captain"] });
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Not authorized" })).toBeInTheDocument(),
    );
  });

  it("blocks a guild_admin from the operator-only audit log", async () => {
    renderAt("/audit", { roles: ["guild_admin"] });
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Not authorized" })).toBeInTheDocument(),
    );
  });

  it("lets any authenticated user reach the global accounts screen", async () => {
    renderAt("/accounts", { roles: ["player"] });
    expect(await screen.findByRole("heading", { name: "Accounts" })).toBeInTheDocument();
  });
});
