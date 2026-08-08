import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { AdminGuild, ApiClient, TokenPair } from "@lol/clients-ts";
import { ApiError } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import ServersScreen from "./ServersScreen";

const INSTALLED: AdminGuild = {
  discord_guild_id: "111",
  name: "My Installed Server",
  icon_url: null,
  owner: true,
  installed: true,
  guild_id: "guild-uuid-1",
  invite_url: "https://discord.com/oauth2/authorize?guild_id=111",
};

const NOT_INSTALLED: AdminGuild = {
  discord_guild_id: "222",
  name: "Fresh Server",
  icon_url: null,
  owner: false,
  installed: false,
  guild_id: null,
  invite_url: "https://discord.com/oauth2/authorize?guild_id=222",
};

interface Mocks {
  client: ApiClient;
  adminGuilds: ReturnType<typeof vi.fn>;
  refresh: ReturnType<typeof vi.fn>;
}

function makeClient(rows: AdminGuild[] | Error): Mocks {
  const adminGuilds = vi.fn(async (): Promise<AdminGuild[]> => {
    if (rows instanceof Error) throw rows;
    return rows;
  });
  const refresh = vi.fn(
    async (): Promise<TokenPair> => ({
      access_token: "new-access",
      refresh_token: "new-refresh",
      token_type: "bearer",
    }),
  );
  const client = { auth: { adminGuilds, refresh } } as unknown as ApiClient;
  return { client, adminGuilds, refresh };
}

/** Mount with NO roles — the state a freshly-signed-in user is actually in. */
function renderScreen(client: ApiClient): void {
  render(
    <AppProviders initialSession={makeSession({ sub: "u1", roles: [] })} client={client}>
      <MemoryRouter>
        <ServersScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("ServersScreen", () => {
  beforeEach(() => {
    vi.stubGlobal("open", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a role-less user's servers, split by whether the bot is installed", async () => {
    const { client } = makeClient([INSTALLED, NOT_INSTALLED]);
    renderScreen(client);

    // The whole point: this screen works with roles: [] — no gate, no redirect.
    expect(await screen.findByText("My Installed Server")).toBeInTheDocument();
    expect(screen.getByText("Fresh Server")).toBeInTheDocument();

    expect(screen.getByText("Bot installed")).toBeInTheDocument();
    expect(screen.getByText("Bot not added")).toBeInTheDocument();

    // Only the server missing the bot offers an install action.
    expect(screen.getAllByRole("button", { name: "Add bot" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Manage" })).toHaveLength(1);
  });

  it("opens the backend-supplied invite URL rather than assembling one", async () => {
    const user = userEvent.setup();
    const { client } = makeClient([NOT_INSTALLED]);
    renderScreen(client);

    await user.click(await screen.findByRole("button", { name: "Add bot" }));

    expect(window.open).toHaveBeenCalledWith(
      NOT_INSTALLED.invite_url,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("rotates the token after an install so the new guild_admin role lands", async () => {
    const user = userEvent.setup();
    const { client, adminGuilds, refresh } = makeClient([NOT_INSTALLED]);
    renderScreen(client);

    await screen.findByText("Fresh Server");
    expect(adminGuilds).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "I've added it" }));

    // Without this rotation the access JWT still reads roles: [] — the user
    // would appear to have no access to the server they just installed into.
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(adminGuilds).toHaveBeenCalledTimes(2));
  });

  it("asks the user to sign in again when Discord rejects the stored token", async () => {
    // 409 discord_reauth_required — every account that logged in before the
    // `guilds` scope was added hits exactly this.
    const { client } = makeClient(new ApiError(409, "discord_reauth_required"));
    renderScreen(client);

    expect(
      await screen.findByRole("button", { name: "Sign in with Discord again" }),
    ).toBeInTheDocument();
  });

  it("explains the empty case instead of showing a bare list", async () => {
    const { client } = makeClient([]);
    renderScreen(client);

    expect(await screen.findByText("No servers to show")).toBeInTheDocument();
  });
});
