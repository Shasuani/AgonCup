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
    game_mode: "summoners_rift_5v5",
    best_of: 1,
    max_teams: 8,
    is_shared_to_central: false,
    registration_opens_at: null,
    start_at: null,
  },
  {
    id: "tour-2",
    name: "Summer Clash",
    status: "in_progress",
    format: "single_elimination",
    game_mode: "aram_1v1",
    best_of: 3,
    max_teams: 16,
    is_shared_to_central: true,
    registration_opens_at: null,
    start_at: null,
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
      game_mode: body.game_mode ?? "summoners_rift_5v5",
      best_of: body.best_of ?? 1,
      max_teams: body.max_teams ?? 16,
      is_shared_to_central: false,
      registration_opens_at: body.registration_opens_at ?? null,
      start_at: body.start_at ?? null,
      join_code: "ABCD2345",
    })),
    get: vi.fn(async (id: string) => TOURNAMENTS.find((t) => t.id === id) ?? TOURNAMENTS[0]),
    register: vi.fn(),
    checkIn: vi.fn(),
    start: vi.fn(),
    remove: vi.fn(async () => undefined),
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

/**
 * The create form is behind a toggle now — the screen opens on the tournaments,
 * not on a wall of inputs. Every form test starts by opening it.
 */
async function openCreateForm(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: "New tournament" }));
  await screen.findByLabelText(/Name/);
}

describe("TournamentsScreen list", () => {
  it("renders a row per tournament from the mocked client", async () => {
    const { client, tournaments } = mockClient();
    renderScreen(client);

    expect(await screen.findByRole("button", { name: "Spring Cup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Summer Clash" })).toBeInTheDocument();
    // status + mode + format + series + size columns rendered, with the raw
    // enum values humanised. Neither fixture status is in the tournament label
    // map, so both exercise the humanize() fallback — the point being that a
    // user never sees `in_progress`.
    expect(screen.getByText("Registration")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getAllByText("Single elim").length).toBe(2);
    expect(screen.getByText("5v5 Rift")).toBeInTheDocument();
    expect(screen.getByText("1v1 ARAM")).toBeInTheDocument();
    expect(screen.getByText("Bo3")).toBeInTheDocument();
    expect(screen.getByText("8 teams")).toBeInTheDocument();
    expect(screen.getByText("16 players")).toBeInTheDocument();
    expect(tournaments.list).toHaveBeenCalledTimes(1);
  });

  it("shows the empty message when there are no tournaments", async () => {
    const { client } = mockClient({ list: vi.fn(async () => []) });
    renderScreen(client);
    expect(
      await screen.findByText(/No tournaments yet\. Create one to mint a join code/),
    ).toBeInTheDocument();
  });
});

describe("CreateTournamentForm submit", () => {
  it("creates a tournament and reloads the list", async () => {
    const { client, tournaments } = mockClient();
    renderScreen(client);

    await openCreateForm();

    await userEvent.type(screen.getByLabelText(/Name/), "Autumn Open");
    await userEvent.click(screen.getByRole("button", { name: "Create tournament" }));

    await waitFor(() => expect(tournaments.create).toHaveBeenCalledTimes(1));
    expect(tournaments.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Autumn Open", max_teams: 8 }),
    );
    // list re-fetched after a successful create (initial + reload).
    await waitFor(() => expect(tournaments.list).toHaveBeenCalledTimes(2));
  });

  it("sends the game mode and series the admin picked", async () => {
    // These decide how players enrol (individually vs as a team) and how long a
    // match runs. The form used to omit both, so every tournament was 5v5 Bo1.
    const { client, tournaments } = mockClient();
    renderScreen(client);
    await openCreateForm();

    await userEvent.type(screen.getByLabelText(/Name/), "Duel Night");
    await userEvent.selectOptions(screen.getByLabelText(/Game mode/), "aram_1v1");
    await userEvent.selectOptions(screen.getByLabelText(/Series/), "3");
    await userEvent.click(screen.getByRole("button", { name: "Create tournament" }));

    await waitFor(() => expect(tournaments.create).toHaveBeenCalledTimes(1));
    expect(tournaments.create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Duel Night",
        game_mode: "aram_1v1",
        best_of: 3,
        format: "single_elimination",
      }),
    );
  });

  it("asks no bracket-format question — every tournament is a knockout", async () => {
    // Owner decision (2026-08-25): the single/double-elimination choice is asked
    // nowhere (web or Discord); the form always submits single_elimination.
    const { client } = mockClient();
    renderScreen(client);
    await openCreateForm();

    expect(screen.queryByLabelText(/Format/)).not.toBeInTheDocument();
  });

  it("relabels the bracket size for a solo mode", async () => {
    const { client } = mockClient();
    renderScreen(client);
    await openCreateForm();

    expect(screen.getByLabelText(/Bracket size \(teams\)/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/Game mode/), "aram_1v1");
    expect(screen.getByLabelText(/Bracket size \(players\)/)).toBeInTheDocument();
  });

  it("shows the join code after creating — the API returns it nowhere else", async () => {
    const { client } = mockClient();
    renderScreen(client);
    await openCreateForm();

    await userEvent.type(screen.getByLabelText(/Name/), "Autumn Open");
    await userEvent.click(screen.getByRole("button", { name: "Create tournament" }));

    expect(await screen.findByText("ABCD2345")).toBeInTheDocument();
  });

  it("blocks a start time that precedes registration opening", async () => {
    const { client, tournaments } = mockClient();
    renderScreen(client);
    await openCreateForm();

    await userEvent.type(screen.getByLabelText(/Name/), "Backwards Cup");
    await userEvent.type(screen.getByLabelText(/Registration opens/), "2026-09-02T18:00");
    await userEvent.type(screen.getByLabelText(/Starts/), "2026-09-01T18:00");

    expect(screen.getByRole("button", { name: "Create tournament" })).toBeDisabled();
    expect(tournaments.create).not.toHaveBeenCalled();
  });
});

describe("Create form gating", () => {
  it("is hidden from non-admin roles", async () => {
    const { client } = mockClient();
    renderScreen(client, ["player"]);
    await screen.findByRole("button", { name: "Spring Cup" });
    // Neither the toggle that reveals the form nor the form's submit exists.
    expect(screen.queryByRole("button", { name: "New tournament" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create tournament" })).not.toBeInTheDocument();
  });
});


describe("TournamentsScreen delete", () => {
  /** A draft alongside the live ones — all of them deletable by an admin now. */
  const WITH_DRAFT: TournamentResponse[] = [
    ...TOURNAMENTS,
    {
      id: "tour-draft",
      name: "Scratch Cup",
      status: "draft",
      format: "single_elimination",
      game_mode: "aram_1v1",
      best_of: 1,
      max_teams: 8,
      is_shared_to_central: false,
      registration_opens_at: null,
      start_at: null,
    },
  ];

  /** The Delete button inside the named tournament's row. */
  function deleteButtonIn(name: string): HTMLElement {
    const row = screen.getByRole("button", { name }).closest("tr");
    const button = row?.querySelector("[data-delete-tournament]");
    if (!button) throw new Error(`no delete button in the "${name}" row`);
    return button as HTMLElement;
  }

  it("offers Delete on every row for an admin", async () => {
    const { client } = mockClient({ list: vi.fn(async () => WITH_DRAFT) });
    renderScreen(client);

    await screen.findByText("Scratch Cup");
    await waitFor(() => {
      expect(document.querySelectorAll("[data-delete-tournament]")).toHaveLength(3);
    });
  });

  it("hides Delete from non-admin roles", async () => {
    const { client } = mockClient({ list: vi.fn(async () => WITH_DRAFT) });
    renderScreen(client, ["player"]);

    await screen.findByText("Scratch Cup");
    expect(document.querySelectorAll("[data-delete-tournament]")).toHaveLength(0);
  });

  it("confirms first, then deletes and reloads", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { client, tournaments } = mockClient({ list: vi.fn(async () => WITH_DRAFT) });
    renderScreen(client);

    await screen.findByText("Scratch Cup");
    await userEvent.click(deleteButtonIn("Scratch Cup"));

    await waitFor(() => expect(tournaments.remove).toHaveBeenCalledWith("tour-draft"));
    expect(confirm).toHaveBeenCalled();
    // Reloaded, so the row does not linger after the row is gone server-side.
    await waitFor(() => expect(tournaments.list).toHaveBeenCalledTimes(2));
    confirm.mockRestore();
  });

  it("warns that a live tournament's history goes with it", async () => {
    // Past draft the row holds other people's registrations and results — the
    // confirm has to say that, not read like tidying up a form mistake.
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { client, tournaments } = mockClient({ list: vi.fn(async () => WITH_DRAFT) });
    renderScreen(client);

    await screen.findByText("Summer Clash");
    await userEvent.click(deleteButtonIn("Summer Clash"));

    await waitFor(() => expect(tournaments.remove).toHaveBeenCalledWith("tour-2"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("erased for everyone"));
    confirm.mockRestore();
  });

  it("deletes nothing when the confirm is dismissed", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { client, tournaments } = mockClient({ list: vi.fn(async () => WITH_DRAFT) });
    renderScreen(client);

    await screen.findByText("Scratch Cup");
    await userEvent.click(deleteButtonIn("Scratch Cup"));

    expect(tournaments.remove).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("surfaces a failure instead of silently doing nothing", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { client } = mockClient({
      list: vi.fn(async () => WITH_DRAFT),
      remove: vi.fn(async () => {
        throw new Error("tournament not found");
      }),
    });
    renderScreen(client);

    await screen.findByText("Scratch Cup");
    await userEvent.click(deleteButtonIn("Scratch Cup"));

    expect(await screen.findByText(/tournament not found/)).toBeTruthy();
    confirm.mockRestore();
  });
});
