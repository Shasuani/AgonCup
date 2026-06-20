import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, TournamentResponse } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import { TournamentDetail } from "./TournamentDetail";
import type { BracketMatch } from "../../components/BracketView";

const TOURNAMENT: TournamentResponse = {
  id: "tour-1",
  name: "Spring Cup",
  status: "ready",
  format: "single_elimination",
  max_teams: 8,
  is_shared_to_central: false,
};

function mockClient(overrides: Partial<ApiClient["tournaments"]> = {}): {
  client: ApiClient;
  tournaments: ApiClient["tournaments"];
} {
  const tournaments = {
    get: vi.fn(async () => TOURNAMENT),
    list: vi.fn(),
    create: vi.fn(),
    register: vi.fn(async (_id: string, body: { team_id: string }) => ({
      id: "reg-1",
      tournament_id: "tour-1",
      team_id: body.team_id,
      status: "registered",
    })),
    checkIn: vi.fn(async (_id: string, body: { team_id: string }) => ({
      id: "reg-1",
      tournament_id: "tour-1",
      team_id: body.team_id,
      status: "checked_in",
    })),
    start: vi.fn(async () => ({
      tournament_id: "tour-1",
      matches_created: 7,
      rounds: 3,
      byes: 0,
    })),
    ...overrides,
  } as unknown as ApiClient["tournaments"];

  return { client: { tournaments } as unknown as ApiClient, tournaments };
}

function renderDetail(
  client: ApiClient,
  roles: string[],
  matches: BracketMatch[] = [],
) {
  return render(
    <AppProviders initialSession={makeSession({ sub: "u1", roles })} client={client}>
      <MemoryRouter>
        <TournamentDetail tournamentId="tour-1" matches={matches} />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("TournamentDetail Start gating", () => {
  it("shows the Start action to a guild_admin", async () => {
    const { client } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });
    expect(screen.getByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("hides the Start action from non-admin roles", async () => {
    const { client } = mockClient();
    renderDetail(client, ["captain"]);
    await screen.findByRole("heading", { name: "Spring Cup" });
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });

  it("calls tournaments.start when an admin clicks Start", async () => {
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });

    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(tournaments.start).toHaveBeenCalledWith("tour-1"));
    expect(await screen.findByText(/bracket generated/i)).toBeInTheDocument();
  });
});

describe("TournamentDetail registration + check-in", () => {
  it("registers a team and then checks it in", async () => {
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });

    await userEvent.type(screen.getByLabelText(/Team ID/), "team-42");
    await userEvent.click(screen.getByRole("button", { name: "Register team" }));

    await waitFor(() =>
      expect(tournaments.register).toHaveBeenCalledWith("tour-1", { team_id: "team-42" }),
    );
    expect(await screen.findByText("registered")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Check in" }));
    await waitFor(() =>
      expect(tournaments.checkIn).toHaveBeenCalledWith("tour-1", { team_id: "team-42" }),
    );
    expect(await screen.findByText("checked_in")).toBeInTheDocument();
  });
});
