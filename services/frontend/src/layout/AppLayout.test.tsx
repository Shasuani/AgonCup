// The app shell's navigation.
//
// There is no drawer and no hamburger any more. The sidebar is always rendered
// and always reachable; below 900px CSS lays it out as a horizontal strip. That
// is the property worth locking down: whatever the viewport, the nav links are
// in the document, focusable, and not hidden behind a control.

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ApiClient } from "@lol/clients-ts";
import { AppProviders } from "../App";
import { makeSession } from "../test/jwt";
import { AppLayout } from "./AppLayout";

function renderLayout(): void {
  const client = { auth: {}, teams: {}, tournaments: {} } as unknown as ApiClient;
  render(
    <AppProviders
      initialSession={makeSession({ sub: "u1", roles: ["guild_admin"], guild_id: "g1" })}
      initialGuildId="g1"
      client={client}
    >
      <MemoryRouter initialEntries={["/teams"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/teams" element={<p>content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("AppLayout navigation", () => {
  it("renders the primary nav links directly, with nothing to open first", () => {
    renderLayout();

    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: /Teams/ })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /Tournaments/ })).toBeInTheDocument();
  });

  it("has no hamburger and no scrim left over", () => {
    renderLayout();

    expect(screen.queryByRole("button", { name: "Open navigation" })).toBeNull();
    expect(document.body.querySelector("[data-app-hamburger]")).toBeNull();
    expect(document.body.querySelector("[data-app-scrim]")).toBeNull();
    expect(document.body.querySelector("[data-app-shell]")).not.toHaveAttribute(
      "data-drawer-open",
    );
  });

  it("never marks the sidebar inert", () => {
    // The old drawer set `inert` while closed. With no drawer there is no state
    // in which the nav may be removed from the tab order.
    renderLayout();

    const sidebar = document.body.querySelector("[data-app-sidebar]");
    expect(sidebar).not.toHaveAttribute("inert");
    expect(sidebar).not.toHaveAttribute("aria-hidden");
  });

  it("no longer watches the drawer breakpoint in JS", () => {
    // The layout used to mirror the 899px breakpoint into React state to drive
    // `inert`. The breakpoint now lives only in CSS. ThemeProvider legitimately
    // queries prefers-color-scheme, so assert on the QUERY, not the call count.
    const spy = vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    vi.stubGlobal("matchMedia", spy);
    renderLayout();

    const queries = spy.mock.calls.map((c) => String(c[0]));
    expect(queries.some((q) => q.includes("899px"))).toBe(false);
    vi.unstubAllGlobals();
  });
});
