import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type {
  ApiClient,
  BracketMatchResponse,
  EntrantResponse,
  TournamentResponse,
} from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import { TournamentDetail, toBracketMatch } from "./TournamentDetail";
import type { BracketMatch } from "../../components/BracketView";

const TOURNAMENT: TournamentResponse = {
  id: "tour-1",
  name: "Spring Cup",
  status: "ready",
  format: "single_elimination",
  game_mode: "summoners_rift_5v5",
  best_of: 1,
  max_teams: 8,
  region: null,
  is_shared_to_central: false,
  registration_opens_at: null,
  start_at: null,
};

const ENTRANTS: EntrantResponse[] = [
  {
    team_id: "team-1",
    name: "Alpha",
    locked_in: true,
    is_solo: false,
    is_you: false,
    can_lock_in: true,
    players: [
      { user_id: "u-1", name: "Ana", locked_in: true, is_you: false },
      { user_id: "u-2", name: "Bo", locked_in: true, is_you: false },
    ],
  },
  {
    team_id: "team-2",
    name: "Beta",
    locked_in: false,
    is_solo: false,
    is_you: false,
    can_lock_in: true,
    // Half the roster has not pressed Lock in — this is what the admin's
    // "Lock in" button overrides.
    players: [
      { user_id: "u-3", name: "Cy", locked_in: true, is_you: false },
      { user_id: "u-4", name: "Di", locked_in: false, is_you: false },
    ],
  },
];

function mockClient(overrides: Partial<ApiClient["tournaments"]> = {}): {
  client: ApiClient;
  tournaments: ApiClient["tournaments"];
} {
  const tournaments = {
    get: vi.fn(async () => TOURNAMENT),
    list: vi.fn(),
    create: vi.fn(),
    entrants: vi.fn(async () => ENTRANTS),
    bracket: vi.fn(async () => ({ tournament: TOURNAMENT, matches: [] })),
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
  matches?: BracketMatch[],
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
  it("lists the entrants the server reports, with their lock-in state", async () => {
    // Previously this list held only registrations made in THIS browser session,
    // so a reload showed "nobody has entered" on a full bracket.
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("locked in")).toBeInTheDocument();
    // Lock-in is per player, so a team that is not ready says WHO is missing —
    // "not ready" alone left an admin with no way to chase the holdout.
    expect(screen.getByText("1/2 locked in")).toBeInTheDocument();
    expect(tournaments.entrants).toHaveBeenCalledWith("tour-1");
    // Only the entrant that has NOT locked in offers the action.
    expect(screen.getAllByRole("button", { name: "Lock in" })).toHaveLength(1);
  });

  it("locks an entrant in and re-reads the list", async () => {
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByText("Beta");

    await userEvent.click(screen.getByRole("button", { name: "Lock in" }));

    await waitFor(() =>
      expect(tournaments.checkIn).toHaveBeenCalledWith("tour-1", { team_id: "team-2" }),
    );
    await waitFor(() => expect(tournaments.entrants).toHaveBeenCalledTimes(2));
  });

  it("registers a team by id through the admin escape hatch", async () => {
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });

    await userEvent.type(screen.getByLabelText(/Team ID/), "team-42");
    await userEvent.click(screen.getByRole("button", { name: "Register team" }));

    await waitFor(() =>
      expect(tournaments.register).toHaveBeenCalledWith("tour-1", { team_id: "team-42" }),
    );
    await waitFor(() => expect(tournaments.entrants).toHaveBeenCalledTimes(2));
  });
});

describe("TournamentDetail bracket", () => {
  const RUNNING: TournamentResponse = { ...TOURNAMENT, status: "running" };
  const MATCHES: BracketMatchResponse[] = [
    { id: "m-1", round: 1, slot: 0, team_a_id: "team-1", team_a_name: "Alpha", team_b_id: "team-2", team_b_name: "Beta", winner_team_id: "team-1", status: "completed" },
    { id: "m-2", round: 1, slot: 1, team_a_id: "team-3", team_a_name: "Gamma", team_b_id: null, team_b_name: null, winner_team_id: "team-3", status: "completed" },
    { id: "m-3", round: 2, slot: 0, team_a_id: "team-1", team_a_name: "Alpha", team_b_id: "team-3", team_b_name: "Gamma", winner_team_id: null, status: "pending" },
  ];

  it("draws the bracket the server returns", async () => {
    // It used to render only a `matches` prop nobody passed: a running
    // tournament said "No bracket yet".
    const { client, tournaments } = mockClient({
      get: vi.fn(async () => RUNNING),
      bracket: vi.fn(async () => ({ tournament: RUNNING, matches: MATCHES })),
    } as Partial<ApiClient["tournaments"]>);
    renderDetail(client, ["guild_admin"]);

    const section = (await screen.findByRole("heading", { name: "Bracket" })).closest("section")!;
    await waitFor(() => expect(within(section).getAllByText("Gamma").length).toBeGreaterThan(0));
    expect(within(section).getAllByText("Alpha").length).toBeGreaterThan(0);
    expect(within(section).getByText("Beta")).toBeInTheDocument();
    expect(tournaments.bracket).toHaveBeenCalledWith("tour-1");
  });

  it("does not offer Start once the tournament is running", async () => {
    const { client } = mockClient({ get: vi.fn(async () => RUNNING) } as Partial<ApiClient["tournaments"]>);
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });

  it("re-reads the bracket after Start", async () => {
    const { client, tournaments } = mockClient();
    renderDetail(client, ["guild_admin"]);
    await screen.findByRole("heading", { name: "Spring Cup" });
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(tournaments.bracket).toHaveBeenCalledTimes(2));
  });
});

describe("toBracketMatch", () => {
  it("maps slot -> position and a missing team to an empty slot", () => {
    expect(
      toBracketMatch({ id: "m", round: 1, slot: 3, team_a_id: "a", team_a_name: "A", team_b_id: null, team_b_name: null, winner_team_id: "a", status: "completed" }),
    ).toEqual({ id: "m", round: 1, position: 3, slotA: { teamId: "a", name: "A" }, slotB: null, winnerTeamId: "a" });
  });
});
