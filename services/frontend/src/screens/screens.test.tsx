import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { createClient, type ApiClient } from "@lol/clients-ts";
import TournamentsScreen from "./tournaments/TournamentsScreen";
import TeamsScreen from "./teams/TeamsScreen";
import AccountsScreen from "./accounts/AccountsScreen";
import DisputeQueueScreen from "./results/DisputeQueueScreen";
import AuditLogScreen from "./audit/AuditLogScreen";
import HomeScreen from "./HomeScreen";
import NotAuthorizedScreen from "./auth/NotAuthorizedScreen";
import { AppProviders } from "../App";
import { makeSession } from "../test/jwt";

// A client whose fetch never resolves: the data-backed screens (disputes,
// audit) mount in their loading state without hitting the network, which is all
// these smoke tests assert (the heading renders).
function pendingClient(): ApiClient {
  return createClient({
    baseUrl: "https://api.test",
    fetch: (() => new Promise<Response>(() => {})) as unknown as typeof fetch,
  });
}

describe("screen stubs render", () => {
  it.each([
    ["Tournaments", <TournamentsScreen key="t" />],
  ])("renders %s", (heading, element) => {
    render(element);
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it.each([
    // Teams + Accounts now talk to the API client (no longer stubs).
    ["Teams", <TeamsScreen key="te" />],
    ["Linked accounts", <AccountsScreen key="a" />],
    ["Dispute / Override queue", <DisputeQueueScreen key="d" />],
    ["Audit log", <AuditLogScreen key="au" />],
  ])("renders %s", (heading, element) => {
    render(
      <AppProviders
        initialSession={makeSession({
          sub: "u1",
          roles: ["platform_operator"],
          guild_id: "g1",
        })}
        initialGuildId="g1"
        client={pendingClient()}
      >
        <MemoryRouter>{element}</MemoryRouter>
      </AppProviders>,
    );
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("HomeScreen renders nav links", () => {
    render(
      <MemoryRouter>
        <HomeScreen />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tournaments" })).toBeInTheDocument();
  });

  it("NotAuthorizedScreen renders", () => {
    render(
      <MemoryRouter>
        <NotAuthorizedScreen />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Not authorized" })).toBeInTheDocument();
  });
});
