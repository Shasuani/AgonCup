import { describe, expect, it } from "vitest";
import type {
  GameMode,
  LobbyPhase,
  ResultDecidedBy,
  TeamMemberRole,
  TournamentFormat,
} from "@lol/clients-ts";
import {
  DECIDED_BY_LABELS,
  FORMAT_LABELS,
  GAME_MODE_LABELS,
  LOBBY_PHASE_LABELS,
  TEAM_ROLE_LABELS,
  TOURNAMENT_STATUS_LABELS,
  entrantsText,
  humanize,
  label,
  regionName,
  seriesText,
} from "./labels";

describe("labels", () => {
  it("maps known enum values to human text", () => {
    expect(label(TOURNAMENT_STATUS_LABELS, "check_in")).toBe("Check-in");
    expect(label(TOURNAMENT_STATUS_LABELS, "running")).toBe("Running");
  });

  it("falls back to a humanized form for values it does not know", () => {
    // A status added server-side must still render readably — never blank, and
    // never a wrong guess at what it means.
    expect(label(TOURNAMENT_STATUS_LABELS, "seeding_paused")).toBe("Seeding paused");
    expect(humanize("admin_override")).toBe("Admin override");
  });

  it("leaves an already-plain value alone", () => {
    expect(humanize("draft")).toBe("Draft");
  });

  it("resolves region codes to server names, case-insensitively", () => {
    expect(regionName("euw1")).toBe("EU West");
    expect(regionName("EUN1")).toBe("EU Nordic & East");
    expect(regionName("zz9")).toBeUndefined();
    expect(regionName(null)).toBeUndefined();
  });

  it("spells out a series and pluralizes entrants by game mode", () => {
    expect(seriesText(1)).toBe("Single game");
    expect(seriesText(3)).toBe("Best of 3");
    expect(entrantsText(1, "aram_1v1")).toBe("1 player");
    expect(entrantsText(8, "summoners_rift_5v5")).toBe("8 teams");
  });
});

// ---------------------------------------------------------------------------
// Drift guard: the wire unions vs the label maps.
//
// The client types in libs/lol-clients-ts are hand-mirrored from the backend
// routers, and they HAVE drifted: `TeamMemberRole` once said `substitute` where
// the enum says `sub` (a guaranteed 422), and `LobbyPhase` listed four values
// that do not exist while omitting eight that do. Both shipped unnoticed because
// nothing compared them to anything.
//
// This pins each union against its label map in BOTH directions:
//   * `satisfies readonly T[]` rejects a listed value that is not in the union;
//   * the `Exclude<...> extends never` check rejects a union member left out;
//   * the runtime assertion rejects a value with no human label.
// A value added to either side alone now fails the build.
//
// It does NOT prove the union matches the BACKEND — only `make gen-clients`
// closes that gap. It catches the half we can see from here.
// ---------------------------------------------------------------------------

/** Compile error if `Listed` misses any member of `Union`. */
type Exhaustive<Union, Listed> = Exclude<Union, Listed> extends never ? true : never;

const LOBBY_PHASES = [
  "assigned_host",
  "awaiting_create",
  "lobby_open",
  "filling",
  "ready",
  "starting",
  "in_game",
  "result_pending",
  "ended",
  "aborted",
  "host_lost",
] as const satisfies readonly LobbyPhase[];

const TEAM_ROLES = ["captain", "player", "sub"] as const satisfies readonly TeamMemberRole[];

const DECIDED_BY = [
  "quorum",
  "admin_override",
  "walkover",
  "forfeit",
] as const satisfies readonly ResultDecidedBy[];

const FORMATS = [
  "single_elimination",
  "double_elimination",
] as const satisfies readonly TournamentFormat[];

const GAME_MODES = ["summoners_rift_5v5", "aram_1v1"] as const satisfies readonly GameMode[];

// These fail to COMPILE if a union member is missing from the list above.
const _phasesComplete: Exhaustive<LobbyPhase, (typeof LOBBY_PHASES)[number]> = true;
const _rolesComplete: Exhaustive<TeamMemberRole, (typeof TEAM_ROLES)[number]> = true;
const _decidedComplete: Exhaustive<ResultDecidedBy, (typeof DECIDED_BY)[number]> = true;
const _formatsComplete: Exhaustive<TournamentFormat, (typeof FORMATS)[number]> = true;
const _modesComplete: Exhaustive<GameMode, (typeof GAME_MODES)[number]> = true;
void [_phasesComplete, _rolesComplete, _decidedComplete, _formatsComplete, _modesComplete];

const LABELLED_ENUMS = [
  ["LobbyPhase", LOBBY_PHASES, LOBBY_PHASE_LABELS],
  ["TeamMemberRole", TEAM_ROLES, TEAM_ROLE_LABELS],
  ["ResultDecidedBy", DECIDED_BY, DECIDED_BY_LABELS],
  ["TournamentFormat", FORMATS, FORMAT_LABELS],
  ["GameMode", GAME_MODES, GAME_MODE_LABELS],
] as const;

describe("enum unions stay in step with their label maps", () => {
  // Exact set equality, so the check runs both ways at once: a wire value with
  // no label (renders as a raw enum string), and a label for a value the wire
  // cannot send — the `substitute` bug in its dormant form, harmless to render
  // but an invitation to SEND it.
  it.each(LABELLED_ENUMS)("%s labels cover exactly the wire values", (_name, values, labels) => {
    expect(Object.keys(labels).sort()).toEqual([...values].sort());
  });
});
