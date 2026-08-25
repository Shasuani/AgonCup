// Create-tournament form (guild_admin). Submits via tournaments.create and
// notifies the caller on success so the list can refresh. Uses the shared
// FormField/TextInput/Button + useAsyncAction for in-flight + error state.
//
// The form deliberately exposes every option the backend accepts EXCEPT the
// bracket format. It used to ask for name/format/max_teams only, which silently
// created 5v5 Bo1 tournaments — the game mode is the single most consequential
// choice here (it decides whether players enrol individually or as teams) and it
// could not be set at all.
//
// Bracket format is fixed (owner decision, 2026-08-25): the single/double-
// elimination question is asked nowhere (web or Discord) — every tournament is a
// plain knockout bracket. Matches are decided in-game (5v5 at the nexus, 1v1
// ARAM at first blood). The backend still accepts double_elimination over the
// API should it ever be wanted back.
//
// Creating is also where the join code is minted, and the create response is the
// ONLY place the API returns it, so the result is shown inline rather than
// discarded — otherwise an admin who creates from the web has no way to get it.

import { useState, type FormEvent } from "react";
import type {
  CreatedTournamentResponse,
  CreateTournamentRequest,
  GameMode,
} from "@lol/clients-ts";
import { Button, ErrorBanner, FormField, TextInput } from "../../components";
import { useApi } from "../../api/ApiProvider";
import { useAsyncAction } from "../../api/useAsync";
import { REGION_LABELS } from "../../lib/labels";

/** Every tournament is a knockout bracket — see the header comment. */
const KNOCKOUT_FORMAT = "single_elimination" as const;

const MODES: ReadonlyArray<{ value: GameMode; label: string; hint: string }> = [
  {
    value: "summoners_rift_5v5",
    label: "5v5 — Summoner's Rift",
    hint: "Team captains enter their team.",
  },
  {
    value: "aram_1v1",
    label: "1v1 — ARAM duel",
    hint: "Players enter individually; no team needed.",
  },
];

const SERIES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: "Best of 1 (single game)" },
  { value: 3, label: "Best of 3" },
  { value: 5, label: "Best of 5" },
];

// Riot platforms a tournament can be pinned to. Entrants must hold a VERIFIED
// account on the chosen server (an LCU lobby invite is per-server — an EUW
// client cannot invite an EUNE player), so the join code bounces anyone who
// doesn't. EUNE/EUW lead the list (where our guilds live); KR is deliberately
// absent — Korea is out of scope (compliance) and the backend rejects it.
const REGIONS: ReadonlyArray<{ value: string; label: string }> = [
  "eun1",
  "euw1",
  "na1",
  "br1",
  "la1",
  "la2",
  "oc1",
  "tr1",
  "ru",
  "jp1",
].map((value) => ({
  value,
  label: `${value.toUpperCase()} — ${REGION_LABELS[value] ?? value}`,
}));

/** Pre-selected server — EUNE, where our guilds live. */
const DEFAULT_REGION = "eun1";

/** `datetime-local` gives "2026-08-01T18:00" (no zone). The API wants UTC ISO,
 *  and the field is labelled UTC, so stamp the zone rather than convert. */
function toUtcIso(local: string): string | undefined {
  if (local.trim() === "") return undefined;
  const withSeconds = local.length === 16 ? `${local}:00` : local;
  return `${withSeconds}Z`;
}

export interface CreateTournamentFormProps {
  /** Called with the created tournament after a successful submit. */
  onCreated?: (tournament: CreatedTournamentResponse) => void;
}

export function CreateTournamentForm({ onCreated }: CreateTournamentFormProps): JSX.Element {
  const api = useApi();
  const [name, setName] = useState("");
  const [gameMode, setGameMode] = useState<GameMode>("summoners_rift_5v5");
  const [bestOf, setBestOf] = useState(1);
  const [region, setRegion] = useState(DEFAULT_REGION);
  const [maxTeams, setMaxTeams] = useState("8");
  const [opensAt, setOpensAt] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [created, setCreated] = useState<CreatedTournamentResponse | null>(null);

  const create = useAsyncAction((body: CreateTournamentRequest) => api.tournaments.create(body));

  const solo = gameMode === "aram_1v1";
  const entrantNoun = solo ? "players" : "teams";

  const trimmedName = name.trim();
  const parsedMax = Number.parseInt(maxTeams, 10);
  const maxValid = Number.isInteger(parsedMax) && parsedMax >= 2 && parsedMax <= 16;
  // Mirrors the backend's own ordering rule so it is caught before a round trip.
  const scheduleValid =
    opensAt === "" || startsAt === "" || new Date(opensAt) <= new Date(startsAt);
  const canSubmit = trimmedName.length > 0 && maxValid && scheduleValid && !create.loading;

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    const opensIso = toUtcIso(opensAt);
    const startIso = toUtcIso(startsAt);
    const result = await create.run({
      name: trimmedName,
      format: KNOCKOUT_FORMAT,
      game_mode: gameMode,
      best_of: bestOf,
      max_teams: parsedMax,
      region,
      ...(opensIso ? { registration_opens_at: opensIso } : {}),
      ...(startIso ? { start_at: startIso } : {}),
    });
    setCreated(result);
    setName("");
    setGameMode("summoners_rift_5v5");
    setBestOf(1);
    setRegion(DEFAULT_REGION);
    setMaxTeams("8");
    setOpensAt("");
    setStartsAt("");
    onCreated?.(result);
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Create tournament" data-create-tournament>
      <div data-form-grid>
        <TextInput
          label="Name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
          placeholder="Summer Cup"
        />

        <FormField
          label="Game mode"
          required
          hint={MODES.find((m) => m.value === gameMode)?.hint}
        >
          {({ inputId, describedBy }) => (
            <select
              id={inputId}
              aria-describedby={describedBy}
              value={gameMode}
              onChange={(e) => setGameMode(e.target.value as GameMode)}
            >
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          )}
        </FormField>

        <FormField label="Series" required hint="Games needed to win a match.">
          {({ inputId, describedBy }) => (
            <select
              id={inputId}
              aria-describedby={describedBy}
              value={String(bestOf)}
              onChange={(e) => setBestOf(Number.parseInt(e.target.value, 10))}
            >
              {SERIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
        </FormField>

        <FormField
          label="Region"
          required
          hint="The server it is played on — entrants need a verified account there."
        >
          {({ inputId, describedBy }) => (
            <select
              id={inputId}
              aria-describedby={describedBy}
              value={region}
              onChange={(e) => setRegion(e.target.value)}
            >
              {REGIONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          )}
        </FormField>

        <TextInput
          label={`Bracket size (${entrantNoun})`}
          type="number"
          min={2}
          max={16}
          required
          value={maxTeams}
          onChange={(e) => setMaxTeams(e.target.value)}
          hint={`How many ${entrantNoun} can enter. 2 to 16.`}
          error={maxTeams.length > 0 && !maxValid ? "Enter a number from 2 to 16." : undefined}
        />
      </div>

      <fieldset data-form-section>
        <legend>Schedule (optional)</legend>
        <p data-hint>
          Leave both empty to open the tournament for entries straight away.
        </p>
        <div data-form-grid>
          <TextInput
            label="Registration opens (UTC)"
            type="datetime-local"
            value={opensAt}
            onChange={(e) => setOpensAt(e.target.value)}
            hint="Entries are rejected before this time."
          />
          <TextInput
            label="Starts (UTC)"
            type="datetime-local"
            value={startsAt}
            onChange={(e) => setStartsAt(e.target.value)}
            hint="Entries close once this time passes."
            error={!scheduleValid ? "Start must be at or after registration opens." : undefined}
          />
        </div>
      </fieldset>

      {create.error ? <ErrorBanner error={create.error} /> : null}

      <Button type="submit" variant="primary" busy={create.loading} disabled={!canSubmit}>
        Create tournament
      </Button>

      {created ? (
        <div data-created-tournament role="status">
          <h3>{created.name} created</h3>
          <p>
            Share this join code — {created.game_mode === "aram_1v1" ? "players" : "team captains"}{" "}
            enter it in the desktop client.
          </p>
          <p data-join-code>{created.join_code}</p>
          <Button
            variant="secondary"
            onClick={() => void navigator.clipboard?.writeText(created.join_code)}
          >
            Copy code
          </Button>
        </div>
      ) : null}
    </form>
  );
}
