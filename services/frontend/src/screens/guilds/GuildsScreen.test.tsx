import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, PurgeResponse } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import GuildsScreen from "./GuildsScreen";

const purgeResponse: PurgeResponse = {
  guild_id: "g1",
  status: "purged",
  removed_at: "2026-01-01T00:00:00Z",
  deleted_rows: { teams: 3 },
  total_deleted: 3,
};

function makeClient(): { client: ApiClient; purgeGuild: ReturnType<typeof vi.fn> } {
  const purgeGuild = vi.fn(async (): Promise<PurgeResponse> => purgeResponse);
  const client = { lifecycle: { purgeGuild } } as unknown as ApiClient;
  return { client, purgeGuild };
}

function renderScreen(roles: string[], client: ApiClient): void {
  render(
    <AppProviders
      initialSession={makeSession({ sub: "u1", roles, guild_id: "g1" })}
      initialGuildId="g1"
      client={client}
    >
      <MemoryRouter>
        <GuildsScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("GuildsScreen", () => {
  it("hides guild administration from non-operators", () => {
    const { client } = makeClient();
    renderScreen(["guild_admin"], client);
    expect(
      screen.getByText("Guild administration is restricted to platform operators."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Settings for/ })).toBeNull();
  });

  it("requires confirmation before purging and only purges for operators", async () => {
    const user = userEvent.setup();
    const { client, purgeGuild } = makeClient();
    renderScreen(["platform_operator"], client);

    // Open the seeded acting guild's settings.
    await user.click(screen.getByRole("button", { name: "Settings for g1" }));

    // Clicking Purge guild opens the confirm dialog but does NOT call the API.
    await user.click(screen.getByRole("button", { name: "Purge guild" }));
    expect(purgeGuild).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog", { name: "Purge guild" });
    // Confirm in the dialog actually purges.
    await user.click(within(dialog).getByRole("button", { name: "Purge" }));

    await waitFor(() => expect(purgeGuild).toHaveBeenCalledWith("g1"));
    // The settings view closes after a successful purge.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Purge guild" })).toBeNull(),
    );
  });
});
