// Create-tournament form (guild_admin). Submits via tournaments.create and
// notifies the caller on success so the list can refresh. Uses the shared
// FormField/TextInput/Button + useAsyncAction for in-flight + error state.

import { useState, type FormEvent } from "react";
import type {
  CreateTournamentRequest,
  TournamentFormat,
  TournamentResponse,
} from "@lol/clients-ts";
import { Button, ErrorBanner, FormField, TextInput } from "../../components";
import { useApi } from "../../api/ApiProvider";
import { useAsyncAction } from "../../api/useAsync";

const FORMATS: ReadonlyArray<{ value: TournamentFormat; label: string }> = [
  { value: "single_elimination", label: "Single elimination" },
  { value: "double_elimination", label: "Double elimination" },
  { value: "round_robin", label: "Round robin" },
  { value: "swiss", label: "Swiss" },
];

export interface CreateTournamentFormProps {
  /** Called with the created tournament after a successful submit. */
  onCreated?: (tournament: TournamentResponse) => void;
}

export function CreateTournamentForm({ onCreated }: CreateTournamentFormProps): JSX.Element {
  const api = useApi();
  const [name, setName] = useState("");
  const [format, setFormat] = useState<TournamentFormat>("single_elimination");
  const [maxTeams, setMaxTeams] = useState("8");

  const create = useAsyncAction((body: CreateTournamentRequest) =>
    api.tournaments.create(body),
  );

  const trimmedName = name.trim();
  const parsedMax = Number.parseInt(maxTeams, 10);
  const maxValid = Number.isInteger(parsedMax) && parsedMax >= 2 && parsedMax <= 16;
  const canSubmit = trimmedName.length > 0 && maxValid && !create.loading;

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    const created = await create.run({
      name: trimmedName,
      format,
      max_teams: parsedMax,
    });
    setName("");
    setFormat("single_elimination");
    setMaxTeams("8");
    onCreated?.(created);
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Create tournament" data-create-tournament>
      <TextInput
        label="Name"
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={120}
      />

      <FormField label="Format">
        {({ inputId, describedBy }) => (
          <select
            id={inputId}
            aria-describedby={describedBy}
            value={format}
            onChange={(e) => setFormat(e.target.value as TournamentFormat)}
          >
            {FORMATS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        )}
      </FormField>

      <TextInput
        label="Max teams"
        type="number"
        min={2}
        max={16}
        required
        value={maxTeams}
        onChange={(e) => setMaxTeams(e.target.value)}
        error={maxTeams.length > 0 && !maxValid ? "Enter a number from 2 to 16." : undefined}
      />

      {create.error ? <ErrorBanner error={create.error} /> : null}

      <Button type="submit" variant="primary" busy={create.loading} disabled={!canSubmit}>
        Create tournament
      </Button>
    </form>
  );
}
