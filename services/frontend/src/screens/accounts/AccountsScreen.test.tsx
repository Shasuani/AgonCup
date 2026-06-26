import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, RosterEntryResponse } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import AccountsScreen from "./AccountsScreen";

const roster: RosterEntryResponse[] = [
  {
    account_id: "acc-1",
    user_id: "u-1",
    discord_name: "ShadowFox",
    game_name: "Faker",
    tag_line: "KR1",
    region: "kr",
    verified: true,
    verified_at: "2026-06-01T00:00:00Z",
    origin_guild_id: "g-1",
    origin_guild_name: "Faker Fan Club",
  },
  {
    account_id: "acc-2",
    user_id: "u-2",
    discord_name: "BlueWizard",
    game_name: "Caps",
    tag_line: "EUW",
    region: "euw1",
    verified: false,
    verified_at: null,
    origin_guild_id: null,
    origin_guild_name: null,
  },
];

function makeClient(list: () => Promise<RosterEntryResponse[]>): ApiClient {
  return { accounts: { list: vi.fn(list) } } as unknown as ApiClient;
}

function renderScreen(client: ApiClient): void {
  render(
    <AppProviders
      initialSession={makeSession({ sub: "u1", roles: ["platform_operator"] })}
      initialGuildId={null}
      client={client}
    >
      <MemoryRouter>
        <AccountsScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("AccountsScreen", () => {
  it("lists linked players with Discord name, Riot ID, server, and status", async () => {
    renderScreen(makeClient(async () => roster));

    const table = await screen.findByRole("table");
    expect(within(table).getByText("ShadowFox")).toBeInTheDocument();
    expect(within(table).getByText("Faker")).toBeInTheDocument();
    expect(within(table).getByText("#KR1")).toBeInTheDocument();
    // Region is upper-cased for display.
    expect(within(table).getByText("KR")).toBeInTheDocument();
    expect(within(table).getByText("EUW1")).toBeInTheDocument();
    // The Discord server the user joined from.
    expect(within(table).getByText("Faker Fan Club")).toBeInTheDocument();
    expect(within(table).getByText(/Verified/)).toBeInTheDocument();
    expect(within(table).getByText(/Pending/)).toBeInTheDocument();
  });

  it("shows an empty state when nobody has linked", async () => {
    renderScreen(makeClient(async () => []));
    await waitFor(() =>
      expect(screen.getByText(/No accounts have been linked/i)).toBeInTheDocument(),
    );
  });

  it("surfaces an error banner when the roster fails to load", async () => {
    renderScreen(
      makeClient(async () => {
        throw new Error("boom");
      }),
    );
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
  });
});
