import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ApiClient } from "@lol/clients-ts";
import { AppProviders } from "../../App";
import { makeSession } from "../../test/jwt";
import DisputeQueueScreen from "./DisputeQueueScreen";
import type { DisputedMatch } from "./disputes";

const DISPUTE: DisputedMatch = {
  match_id: "m-1",
  tournament_id: "t-1",
  tournament_name: "Summer Cup",
  round_label: "Quarterfinal",
  status: "disputed",
  teams: [
    { team_id: "team-a", name: "Alpha Squad" },
    { team_id: "team-b", name: "Bravo Squad" },
  ],
  reports: [
    { reporter: "captain-a", claimed_winner_team_id: "team-a", note: "we won 2-0" },
    { reporter: "captain-b", claimed_winner_team_id: "team-b" },
  ],
  anomalies: [
    { code: "score_mismatch", detail: "conflicting scores reported", severity: "high" },
  ],
};

/** Build a fake client; override only what a test needs. */
function fakeClient(over: Partial<ApiClient> = {}): ApiClient {
  return {
    results: {
      recordWinner: vi.fn(async () => ({
        match_id: "m-1",
        tournament_id: "t-1",
        winner_team_id: "team-a",
        decided_by: "admin_override",
        state: "recorded",
      })),
    },
    request: vi.fn(async () => [DISPUTE]),
    ...over,
  } as unknown as ApiClient;
}

function renderQueue(client: ApiClient, roles: string[] = ["guild_admin"]): void {
  render(
    <AppProviders
      initialSession={makeSession({ sub: "admin-1", roles, guild_id: "g1" })}
      initialGuildId="g1"
      client={client}
    >
      <MemoryRouter>
        <DisputeQueueScreen />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("DisputeQueueScreen", () => {
  it("renders disputed matches from the mocked client (teams, reports, anomalies)", async () => {
    const client = fakeClient();
    renderQueue(client);

    expect(await screen.findByText("Summer Cup")).toBeInTheDocument();
    expect(screen.getByText("Quarterfinal")).toBeInTheDocument();
    // Both teams listed in the teams cell (names also appear on the buttons).
    const teamsList = document.querySelector("[data-teams-list]") as HTMLElement;
    expect(within(teamsList).getByText("Alpha Squad")).toBeInTheDocument();
    expect(within(teamsList).getByText("Bravo Squad")).toBeInTheDocument();
    // Report summary resolves a claimed winner id -> team name.
    expect(screen.getByText("captain-a")).toBeInTheDocument();
    expect(screen.getByText(/we won 2-0/)).toBeInTheDocument();
    // Anomaly flag surfaced.
    expect(screen.getByText("score_mismatch")).toBeInTheDocument();
    expect(screen.getByText(/conflicting scores reported/)).toBeInTheDocument();

    expect(client.request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "GET", path: "/results/disputes" }),
    );
  });

  it("override submits the chosen winner + reason through results.recordWinner", async () => {
    const recordWinner = vi.fn(async () => ({
      match_id: "m-1",
      tournament_id: "t-1",
      winner_team_id: "team-b",
      decided_by: "admin_override",
      state: "recorded",
    }));
    const client = fakeClient({
      results: { recordWinner },
    } as unknown as Partial<ApiClient>);
    renderQueue(client);

    const user = userEvent.setup();
    await screen.findByText("Summer Cup");

    // Open the override confirm for Bravo Squad.
    await user.click(screen.getByRole("button", { name: /Winner: Bravo Squad/ }));

    const dialog = await screen.findByRole("dialog", { name: "Record decided winner" });
    const reasonBox = within(dialog).getByLabelText(/Reason/);
    await user.type(reasonBox, "Replay confirmed Bravo win");

    await user.click(within(dialog).getByRole("button", { name: "Record override" }));

    await waitFor(() =>
      expect(recordWinner).toHaveBeenCalledWith("m-1", {
        winner_team_id: "team-b",
        decided_by: "admin_override",
        reason: "Replay confirmed Bravo win",
      }),
    );
  });

  it("sends reason: null when the reason field is left blank", async () => {
    const recordWinner = vi.fn(async () => ({
      match_id: "m-1",
      tournament_id: "t-1",
      winner_team_id: "team-a",
      decided_by: "admin_override",
      state: "recorded",
    }));
    const client = fakeClient({
      results: { recordWinner },
    } as unknown as Partial<ApiClient>);
    renderQueue(client);

    const user = userEvent.setup();
    await screen.findByText("Summer Cup");
    await user.click(screen.getByRole("button", { name: /Winner: Alpha Squad/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Record override" }));

    await waitFor(() =>
      expect(recordWinner).toHaveBeenCalledWith("m-1", {
        winner_team_id: "team-a",
        decided_by: "admin_override",
        reason: null,
      }),
    );
  });

  it("gates the override action: a non-admin sees no winner buttons", async () => {
    const client = fakeClient();
    renderQueue(client, ["captain"]);

    await screen.findByText("Summer Cup");
    expect(screen.queryByRole("button", { name: /Winner:/ })).not.toBeInTheDocument();
    expect(screen.getByText("Admin only")).toBeInTheDocument();
  });

  it("shows the empty state when the queue is clear", async () => {
    const client = fakeClient({ request: vi.fn(async () => []) } as Partial<ApiClient>);
    renderQueue(client);
    expect(
      await screen.findByText("No disputed matches. The queue is clear."),
    ).toBeInTheDocument();
  });

  it("escapes server-provided strings (no HTML injection)", async () => {
    const xss: DisputedMatch = {
      ...DISPUTE,
      match_id: "m-2",
      tournament_name: "<img src=x onerror=alert(1)>",
      teams: [
        { team_id: "team-a", name: "<script>bad()</script>" },
        { team_id: "team-b", name: "Bravo Squad" },
      ],
      reports: [],
      anomalies: [],
    };
    const client = fakeClient({ request: vi.fn(async () => [xss]) } as Partial<ApiClient>);
    renderQueue(client);

    // The malicious markup renders as literal text, not as elements.
    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(screen.getByText("<script>bad()</script>")).toBeInTheDocument();
    // No injected <script>/<img> element entered the DOM as markup.
    expect(document.querySelector("script")).toBeNull();
  });
});
