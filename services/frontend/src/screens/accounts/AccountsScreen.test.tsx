import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, LinkAccountResponse } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import AccountsScreen from "./AccountsScreen";

const linkResponse: LinkAccountResponse = {
  account_id: "acc-1",
  puuid: "puuid-1",
  verified: false,
  attempt_id: "attempt-1",
  target_icon_id: 29,
  // Far-future expiry so the panel renders the pending instructions.
  expires_at: "2999-01-01T00:00:00Z",
  game_name: "Faker",
  tag_line: "KR1",
};

function makeClient(overrides: Record<string, unknown> = {}): ApiClient {
  const accounts = {
    link: vi.fn(async () => linkResponse),
    verifyStatus: vi.fn(async () => ({
      attempt_id: "attempt-1",
      puuid: "puuid-1",
      status: "pending",
      target_icon_id: 29,
      expires_at: "2999-01-01T00:00:00Z",
      verified: false,
    })),
    ...overrides,
  };
  return { accounts } as unknown as ApiClient;
}

function renderScreen(client: ApiClient): void {
  render(
    <AppProviders
      initialSession={makeSession({ sub: "u1", roles: [] })}
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
  it("shows verification instructions (target icon nonce) after linking", async () => {
    const user = userEvent.setup();
    const client = makeClient();
    renderScreen(client);

    await user.type(screen.getByLabelText(/Game name/), "Faker");
    await user.type(screen.getByLabelText(/Tag line/), "KR1");
    await user.type(screen.getByLabelText(/Region/), "kr");
    await user.click(screen.getByRole("button", { name: "Link account" }));

    await waitFor(() =>
      expect((client.accounts.link as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        gameName: "Faker",
        tagLine: "KR1",
        region: "kr",
      }),
    );

    const panel = await screen.findByRole("region", { name: "Verify ownership" });
    // The verification nonce (target icon id) is shown to the user.
    expect(within(panel).getByText("#29")).toBeInTheDocument();
    expect(within(panel).getByText(/set your profile icon/i)).toBeInTheDocument();
    expect(within(panel).getByText("Faker#KR1")).toBeInTheDocument();
  });
});
