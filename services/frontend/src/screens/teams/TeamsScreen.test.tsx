import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import TeamsScreen from "./TeamsScreen";

/** Minimal mock client: only the team methods the screen touches. */
function makeClient(overrides: Record<string, unknown> = {}): ApiClient {
  const teams = {
    list: vi.fn(async () => []),
    create: vi.fn(async (body: { name: string; captain_user_id: string }) => ({
      id: "team-1",
      name: body.name,
      captain_user_id: body.captain_user_id,
      guild_id: "g1",
    })),
    get: vi.fn(),
    listMembers: vi.fn(async () => []),
    addMember: vi.fn(async (_id: string, body: { user_id: string; role: string }) => ({
      team_id: "team-1",
      user_id: body.user_id,
      role: body.role,
    })),
    removeMember: vi.fn(async () => undefined),
    leave: vi.fn(async () => undefined),
    ...overrides,
  };
  return { teams } as unknown as ApiClient;
}

function renderScreen(client: ApiClient): void {
  render(
    <AppProviders
      initialSession={makeSession({ sub: "u1", roles: ["guild_admin"], guild_id: "g1" })}
      initialGuildId="g1"
      client={client}
    >
      <MemoryRouter>
        <TeamsScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("TeamsScreen", () => {
  // Team creation lives in the desktop client (the creator becomes captain and
  // gets a join code), so the admin SPA deliberately exposes no create affordance.
  it("offers no team creation — the admin SPA only manages existing rosters", async () => {
    const client = makeClient();
    renderScreen(client);

    await screen.findByRole("heading", { name: "Teams" });
    expect(screen.queryByRole("button", { name: "Create team" })).toBeNull();
    expect(client.teams.create as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
  });

  it("adds a member to a team via the mocked client", async () => {
    const user = userEvent.setup();
    const client = makeClient({
      list: vi.fn(async () => [
        { id: "team-1", name: "Alpha", captain_user_id: "cap-1", guild_id: "g1" },
      ]),
    });
    renderScreen(client);

    await user.click(await screen.findByRole("button", { name: "Manage Alpha" }));

    const dialog = await screen.findByRole("dialog", { name: /Roster/ });
    await user.type(within(dialog).getByLabelText(/User ID/), "player-9");
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));

    await waitFor(() =>
      expect((client.teams.addMember as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(
        "team-1",
        { user_id: "player-9", role: "player" },
      ),
    );
  });

  // The dropdown used to offer "substitute" (not the enum value — a 422) and
  // "captain" (never assignable via add_member — a 400). Only the two roles the
  // backend actually accepts may be selectable, and they must POST wire values.
  it("only offers roles add_member accepts, and posts the wire value", async () => {
    const user = userEvent.setup();
    const client = makeClient({
      list: vi.fn(async () => [
        { id: "team-1", name: "Alpha", captain_user_id: "cap-1", guild_id: "g1" },
      ]),
    });
    renderScreen(client);

    await user.click(await screen.findByRole("button", { name: "Manage Alpha" }));
    const dialog = await screen.findByRole("dialog", { name: /Roster/ });

    const select = within(dialog).getByLabelText("Role");
    const values = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["player", "sub"]);

    // Shown as "Substitute", sent as `sub`.
    await user.selectOptions(select, "sub");
    expect(within(select).getByRole("option", { name: "Substitute" })).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText(/User ID/), "player-9");
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));

    await waitFor(() =>
      expect(client.teams.addMember as ReturnType<typeof vi.fn>).toHaveBeenCalledWith("team-1", {
        user_id: "player-9",
        role: "sub",
      }),
    );
  });

  it("shows roster members by display name, keeping the id copyable", async () => {
    const user = userEvent.setup();
    const client = makeClient({
      list: vi.fn(async () => [
        { id: "team-1", name: "Alpha", captain_user_id: "cap-1", guild_id: "g1" },
      ]),
      listMembers: vi.fn(async () => [
        {
          team_id: "team-1",
          user_id: "3f2b1c9e-0000-4000-8000-000000000001",
          role: "captain",
          display_name: "Kostas",
        },
        // No display_name (the JOIN found no user row) — fall back to the id
        // rather than rendering an empty cell.
        { team_id: "team-1", user_id: "nameless-42", role: "sub" },
      ]),
    });
    renderScreen(client);

    await user.click(await screen.findByRole("button", { name: "Manage Alpha" }));
    const dialog = await screen.findByRole("dialog", { name: /Roster/ });

    // Scoped to the roster table — "Substitute" is also a dropdown option.
    const table = within(dialog).getByRole("table", { name: /Members of Alpha/ });
    expect(await within(table).findByText("Kostas")).toBeInTheDocument();
    // The role reads as English, not the raw `sub`/`captain` enum value.
    expect(within(table).getByText("Captain")).toBeInTheDocument();
    expect(within(table).getByText("Substitute")).toBeInTheDocument();
    // The id survives as a copy affordance, and the remove button is named.
    expect(
      within(table).getByRole("button", {
        name: "Copy user id 3f2b1c9e-0000-4000-8000-000000000001",
      }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Remove Kostas" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Remove nameless-42" })).toBeInTheDocument();
  });

  it("reports a failed add without an unhandled rejection, keeping the form filled", async () => {
    const user = userEvent.setup();
    const onUnhandled = vi.fn();
    window.addEventListener("unhandledrejection", onUnhandled);
    const client = makeClient({
      list: vi.fn(async () => [
        { id: "team-1", name: "Alpha", captain_user_id: "cap-1", guild_id: "g1" },
      ]),
      addMember: vi.fn(async () => {
        throw new Error("user already on team");
      }),
    });
    renderScreen(client);

    await user.click(await screen.findByRole("button", { name: "Manage Alpha" }));
    const dialog = await screen.findByRole("dialog", { name: /Roster/ });

    const input = within(dialog).getByLabelText(/User ID/);
    await user.type(input, "player-9");
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));

    expect(await within(dialog).findByText(/user already on team/)).toBeInTheDocument();
    // The failed submit must NOT run the success-path cleanup — the operator
    // keeps what they typed and can retry or correct it.
    expect(input).toHaveValue("player-9");

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onUnhandled).not.toHaveBeenCalled();
    window.removeEventListener("unhandledrejection", onUnhandled);
  });
});

describe("TeamsScreen roster listing", () => {
  const TEAMS = [
    {
      id: "team-1",
      name: "Alpha",
      captain_user_id: "3f2b9c14-77aa-4c2e-9f31-0d1e2a3b4c5d",
      guild_id: "g1",
      captain_name: "ShadowFox",
      member_count: 3,
    },
  ];

  it("names the leader and counts the roster without opening anything", async () => {
    // The list used to show the captain as a bare UUID and gave no hint of how
    // many players a team had — both now come from the LIST route's JOIN.
    renderScreen(makeClient({ list: vi.fn(async () => TEAMS) }));

    const table = await screen.findByRole("table");
    expect(within(table).getByText("ShadowFox")).toBeInTheDocument();
    expect(within(table).getByText("3 players")).toBeInTheDocument();
  });

  it("says so when the server did not compute a member count", async () => {
    // null is "not fetched on this route" — printing 0 would read as an empty
    // team, which is a different and wrong claim.
    renderScreen(
      makeClient({
        list: vi.fn(async () => [{ ...TEAMS[0], member_count: null, captain_name: null }]),
      }),
    );

    const table = await screen.findByRole("table");
    expect(within(table).getByText("—")).toBeInTheDocument();
    expect(within(table).getByText("Unknown")).toBeInTheDocument();
  });

  it("expands a row to reveal who else is on the team", async () => {
    const user = userEvent.setup();
    const listMembers = vi.fn(async () => [
      { team_id: "team-1", user_id: "u-cap", role: "captain", display_name: "ShadowFox" },
      { team_id: "team-1", user_id: "u-2", role: "player", display_name: "BlueWizard" },
      { team_id: "team-1", user_id: "u-3", role: "sub", display_name: "Kestrel" },
    ]);
    renderScreen(makeClient({ list: vi.fn(async () => TEAMS), listMembers }));

    await screen.findByRole("table");
    // Members are NOT fetched until the row is opened.
    expect(listMembers).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("button", { name: "Show the roster of Alpha" }),
    );

    expect(await screen.findByText("BlueWizard")).toBeInTheDocument();
    expect(screen.getByText("Kestrel")).toBeInTheDocument();
    // Roles are shown as human labels, not the raw `sub` enum value.
    expect(screen.getByText("Substitute")).toBeInTheDocument();
    expect(listMembers).toHaveBeenCalledWith("team-1");
  });

  it("collapses the row again on a second click", async () => {
    const user = userEvent.setup();
    renderScreen(
      makeClient({
        list: vi.fn(async () => TEAMS),
        listMembers: vi.fn(async () => [
          { team_id: "team-1", user_id: "u-2", role: "player", display_name: "BlueWizard" },
        ]),
      }),
    );

    await screen.findByRole("table");
    const toggle = screen.getByRole("button", { name: "Show the roster of Alpha" });

    await user.click(toggle);
    expect(await screen.findByText("BlueWizard")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await user.click(toggle);
    await waitFor(() => expect(screen.queryByText("BlueWizard")).toBeNull());
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});
