import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient, TournamentResponse } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import TournamentsScreen from "./TournamentsScreen";

const TOURNAMENTS: TournamentResponse[] = [
  {
    id: "tour-1",
    name: "Spring Cup",
    status: "registration",
    format: "single_elimination",
    max_teams: 8,
    is_shared_to_central: false,
  },
  {
    id: "tour-2",
    name: "Summer Clash",
    status: "in_progress",
    format: "single_elimination",
    max_teams: 16,
    is_shared_to_central: true,
  },
];

/** A mock client exposing only the tournaments surface the screen touches. */
function mockClient(overrides: Partial<ApiClient["tournaments"]> = {}): {
  client: ApiClient;
  tournaments: ApiClient["tournaments"];
} {
  const tournaments = {
    list: vi.fn(async () => TOURNAMENTS),
    create: vi.fn(async (body) => ({
      id: "tour-new",
      name: body.name,
      status: "draft",
      format: body.format ?? "single_elimination",
      max_teams: body.max_teams ?? 16,
      is_shared_to_central: false,
    })),
    get: vi.fn(async (id: string) => TOURNAMENTS.find((t) => t.id === id) ?? TOURNAMENTS[0]),
    register: vi.fn(),
    checkIn: vi.fn(),
    start: vi.fn(),
    ...overrides,
  } as unknown as ApiClient["tournaments"];

  const client = { tournaments } as unknown as ApiClient;
  return { client, tournaments };
}

function renderScreen(client: ApiClient, roles: string[] = ["guild_admin"]) {
  return render(
    <AppProviders initialSession={makeSession({ sub: "u1", roles })} client={client}>
      <MemoryRouter>
        <TournamentsScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("TournamentsScreen list", () => {
  it("renders a row per tournament from the mocked client", async () => {
    const { client, tournaments } = mockClient();
    renderScreen(client);

    expect(await screen.findByRole("button", { name: "Spring Cup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Summer Clash" })).toBeInTheDocument();
    // status + format + teams columns rendered.
    expect(screen.getByText("registration")).toBeInTheDocument();
    expect(screen.getByText("in_progress")).toBeInTheDocument();
    expect(screen.getAllByText("single_elimination").length).toBe(2);
    expect(tournaments.list).toHaveBeenCalledTimes(1);
  });

  it("shows the empty message when there are no tournaments", async () => {
    const { client } = mockClient({ list: vi.fn(async () => []) });
    renderScreen(client);
    expect(await screen.findByText("No tournaments yet.")).toBeInTheDocument();
  });
});

describe("CreateTournamentForm submit", () => {
  it("creates a tournament and reloads the list", async () => {
    const { client, tournaments } = mockClient();
    renderScreen(client);

    await screen.findByRole("button", { name: "Spring Cup" });

    await userEvent.type(screen.getByLabelText(/Name/), "Autumn Open");
    await userEvent.click(screen.getByRole("button", { name: "Create tournament" }));

    await waitFor(() => expect(tournaments.create).toHaveBeenCalledTimes(1));
    expect(tournaments.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Autumn Open", max_teams: 8 }),
    );
    // list re-fetched after a successful create (initial + reload).
    await waitFor(() => expect(tournaments.list).toHaveBeenCalledTimes(2));
  });
});

describe("Create form gating", () => {
  it("is hidden from non-admin roles", async () => {
    const { client } = mockClient();
    renderScreen(client, ["player"]);
    await screen.findByRole("button", { name: "Spring Cup" });
    expect(screen.queryByRole("button", { name: "Create tournament" })).not.toBeInTheDocument();
  });
});
