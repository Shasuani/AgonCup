import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("counts the roster totals from the loaded rows", async () => {
    renderScreen(makeClient(async () => roster));

    const totals = await screen.findByRole("group", { name: "Roster totals" });
    // Cards expose "<label>: <value>" as their accessible name, so the counts
    // are asserted as the rendered metric, not by scraping the number alone.
    // The FIRST one must be awaited: the grid renders immediately in its
    // loading state, so finding the group proves nothing about the counts yet.
    expect(await within(totals).findByLabelText("Linked players: 2")).toBeInTheDocument();
    expect(within(totals).getByLabelText("Verified: 1")).toBeInTheDocument();
    expect(within(totals).getByLabelText("Pending: 1")).toBeInTheDocument();
  });

  it("filters the table by the search box", async () => {
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(
      screen.getByRole("searchbox", { name: /search linked accounts/i }),
      "caps",
    );

    const table = screen.getByRole("table");
    expect(within(table).getByText("BlueWizard")).toBeInTheDocument();
    expect(within(table).queryByText("ShadowFox")).not.toBeInTheDocument();
  });

  it("filters the table by verification status", async () => {
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    const filters = screen.getByRole("group", {
      name: /filter by verification status/i,
    });
    await userEvent.click(within(filters).getByRole("button", { name: "Pending" }));

    const table = screen.getByRole("table");
    expect(within(table).getByText("BlueWizard")).toBeInTheDocument();
    expect(within(table).queryByText("ShadowFox")).not.toBeInTheDocument();
  });

  it("offers a way back when the filters match nothing", async () => {
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(
      screen.getByRole("searchbox", { name: /search linked accounts/i }),
      "nobody",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /clear filters/i }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
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

describe("AccountsScreen search scope", () => {
  const box = (): HTMLElement =>
    screen.getByRole("searchbox", { name: /search linked accounts/i });

  it("matches the Discord server a player joined from", async () => {
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(box(), "faker fan");

    const table = screen.getByRole("table");
    expect(within(table).getByText("ShadowFox")).toBeInTheDocument();
    // BlueWizard has no origin guild, so it must drop out.
    expect(within(table).queryByText("BlueWizard")).not.toBeInTheDocument();
  });

  it("matches a full Riot ID including the tag", async () => {
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(box(), "faker#kr1");
    expect(within(screen.getByRole("table")).getByText("ShadowFox")).toBeInTheDocument();
  });

  it("does not search the region", async () => {
    // Typing a platform code used to filter the roster. Nobody thinks of "eun1"
    // as a search term, and it made the box feel like it matched anything.
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(box(), "euw1");
    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
  });

  it("does not match across two different fields", async () => {
    // The old implementation joined every field into one string, so a query
    // spanning a boundary matched a value that does not exist on any row.
    renderScreen(makeClient(async () => roster));
    await screen.findByRole("table");

    await userEvent.type(box(), "shadowfox faker");
    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
  });
});
