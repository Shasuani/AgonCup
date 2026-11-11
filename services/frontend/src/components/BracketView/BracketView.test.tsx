import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BracketView } from "./BracketView";
import { groupByRound, isBye, effectiveWinnerId } from "./bracket";
import type { BracketMatch } from "./types";

/**
 * Build a complete N=8 single-elimination bracket:
 *   Round 1: 4 matches (8 teams), Round 2: 2 matches, Round 3 (Final): 1 match.
 * Round-1 match 0 has a decided winner; the rest are undecided/empty downstream.
 */
function buildEightTeamBracket(): BracketMatch[] {
  const team = (n: number) => ({ teamId: `t${n}`, name: `Team ${n}` });
  return [
    // Round 1
    { id: "m1", round: 1, position: 0, slotA: team(1), slotB: team(2), winnerTeamId: "t1" },
    { id: "m2", round: 1, position: 1, slotA: team(3), slotB: team(4), winnerTeamId: null },
    { id: "m3", round: 1, position: 2, slotA: team(5), slotB: team(6), winnerTeamId: null },
    { id: "m4", round: 1, position: 3, slotA: team(7), slotB: team(8), winnerTeamId: null },
    // Round 2 (semifinals) — slots fill as round 1 resolves
    { id: "m5", round: 2, position: 0, slotA: team(1), slotB: null, winnerTeamId: null },
    { id: "m6", round: 2, position: 1, slotA: null, slotB: null, winnerTeamId: null },
    // Round 3 (final)
    { id: "m7", round: 3, position: 0, slotA: null, slotB: null, winnerTeamId: null },
  ];
}

describe("bracket helpers", () => {
  it("groups a flat N=8 list into 3 ordered rounds", () => {
    const rounds = groupByRound(buildEightTeamBracket());
    expect(rounds.map((r) => r.round)).toEqual([1, 2, 3]);
    expect(rounds.map((r) => r.matches.length)).toEqual([4, 2, 1]);
    // Round 1 ordered by position.
    expect(rounds[0]?.matches.map((m) => m.id)).toEqual(["m1", "m2", "m3", "m4"]);
  });

  it("treats a single-filled ROUND-1 match as a bye and auto-advances the lone team", () => {
    const bye: BracketMatch = {
      id: "b",
      round: 1,
      position: 0,
      slotA: { teamId: "t1", name: "Team 1" },
      slotB: null,
      winnerTeamId: null,
    };
    expect(isBye(bye)).toBe(true);
    expect(effectiveWinnerId(bye)).toBe("t1");
  });

  it("does NOT treat a later-round match with one side filled as a bye", () => {
    // Its opponent is still being decided upstream (e.g. a final waiting on the
    // second semifinal) — nobody has advanced yet.
    const waiting: BracketMatch = {
      id: "f",
      round: 3,
      position: 0,
      slotA: { teamId: "t1", name: "Team 1" },
      slotB: null,
      winnerTeamId: null,
    };
    expect(isBye(waiting)).toBe(false);
    expect(effectiveWinnerId(waiting)).toBeNull();
  });
});

describe("BracketView (N=8)", () => {
  it("renders all 8 teams, 3 rounds, and 7 matches", () => {
    render(<BracketView matches={buildEightTeamBracket()} />);

    for (let n = 1; n <= 8; n += 1) {
      expect(screen.getAllByText(`Team ${n}`).length).toBeGreaterThanOrEqual(1);
    }

    // 3 round columns; the last is labelled "Final".
    expect(screen.getByRole("heading", { name: "Round 1" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Round 2" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Final" })).toBeInTheDocument();

    // 7 match list items.
    const matchItems = document.querySelectorAll("[data-bracket-match]");
    expect(matchItems.length).toBe(7);
  });

  it("highlights the decided winner of round-1 match 1", () => {
    const { container } = render(<BracketView matches={buildEightTeamBracket()} />);
    const winners = container.querySelectorAll<HTMLElement>(
      "[data-bracket-slot][data-winner='true']",
    );
    // Only m1's decided winner. m5 (round 2, t1 vs a still-undecided opponent)
    // is not a bye, so t1 is not shown as having won it.
    expect(winners.length).toBe(1);
    const [firstWinner] = winners;
    if (!firstWinner) throw new Error("expected a highlighted winner slot");
    expect(within(firstWinner).getByText("Team 1")).toBeInTheDocument();
  });

  it("labels a round-1 bye and does not offer an override for it", () => {
    const withBye: BracketMatch[] = [
      { id: "r1a", round: 1, position: 0, slotA: { teamId: "t1", name: "Team 1" }, slotB: null, winnerTeamId: "t1" },
      { id: "r1b", round: 1, position: 1, slotA: { teamId: "t2", name: "Team 2" }, slotB: { teamId: "t3", name: "Team 3" }, winnerTeamId: null },
      { id: "r2", round: 2, position: 0, slotA: { teamId: "t1", name: "Team 1" }, slotB: null, winnerTeamId: null },
    ];
    render(<BracketView matches={withBye} onOverride={() => {}} />);
    expect(screen.getAllByText("Bye — auto-advances").length).toBe(1);
    // The final waits on r1b: its empty side reads TBD, not a bye.
    expect(screen.getByText("TBD")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Force winner" })).toHaveLength(1);
  });

  it("shows no bye in the 8-team bracket — every later empty side is TBD", () => {
    render(<BracketView matches={buildEightTeamBracket()} />);
    expect(screen.queryByText("Bye — auto-advances")).not.toBeInTheDocument();
    // m5 has one TBD side, m6 and m7 two each.
    expect(screen.getAllByText("TBD")).toHaveLength(5);
  });

  it("offers a Force winner override only for undecided two-sided matches", async () => {
    const onOverride = vi.fn();
    render(<BracketView matches={buildEightTeamBracket()} onOverride={onOverride} />);
    // Undecided two-sided matches: m2, m3, m4 => 3 override buttons.
    const overrides = screen.getAllByRole("button", { name: "Force winner" });
    expect(overrides.length).toBe(3);

    const [firstOverride] = overrides;
    if (!firstOverride) throw new Error("expected an override button");
    await userEvent.click(firstOverride);
    expect(onOverride).toHaveBeenCalledTimes(1);
    expect(onOverride.mock.calls[0]?.[0]).toMatchObject({ id: "m2" });
  });

  it("renders an empty state when there are no matches", () => {
    render(<BracketView matches={[]} />);
    expect(screen.getByText(/no bracket yet/i)).toBeInTheDocument();
  });
});
