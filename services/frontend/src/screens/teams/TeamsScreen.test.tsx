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
  it("creates a team via the mocked client", async () => {
    const user = userEvent.setup();
    const client = makeClient();
    renderScreen(client);

    await user.click(screen.getByRole("button", { name: "Create team" }));

    const dialog = await screen.findByRole("dialog", { name: "Create team" });
    await user.type(within(dialog).getByLabelText(/Team name/), "Alpha");
    await user.type(within(dialog).getByLabelText(/Captain user ID/), "cap-1");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect((client.teams.create as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        name: "Alpha",
        captain_user_id: "cap-1",
      }),
    );
    // List reloads after create.
    await waitFor(() =>
      expect((client.teams.list as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1),
    );
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
});
