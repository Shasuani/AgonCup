import { describe, expect, it } from "vitest";
import { championIconUrl, kdaText, playerLabel, type HistoryPlayer } from "./matchHistory";

function player(overrides: Partial<HistoryPlayer> = {}): HistoryPlayer {
  return {
    puuid: "0123456789abcdef",
    champion_id: 1,
    kills: 7,
    deaths: 2,
    assists: 9,
    ...overrides,
  };
}

describe("playerLabel", () => {
  it("prefers the Discord display name", () => {
    expect(playerLabel(player({ display_name: "Chama", riot_id: "C#EUNE" }))).toBe("Chama");
  });

  it("falls back to the Riot ID, then a short PUUID", () => {
    expect(playerLabel(player({ display_name: null, riot_id: "C#EUNE" }))).toBe("C#EUNE");
    expect(playerLabel(player({ display_name: null, riot_id: null }))).toBe("01234567…");
  });

  it("handles a missing PUUID", () => {
    expect(playerLabel(player({ display_name: null, riot_id: null, puuid: "" }))).toBe("Unknown");
  });
});

describe("kdaText", () => {
  it("renders k / d / a", () => {
    expect(kdaText(player())).toBe("7 / 2 / 9");
  });
});

describe("championIconUrl", () => {
  it("addresses the icon by numeric id", () => {
    expect(championIconUrl(266)).toContain("/champion-icons/266.png");
  });
});
