// Tournament detail view: summary, registrations + check-in, an admin-only
// Start action (generates the bracket), and the bracket visualizer with a
// manual-override entry point that routes to the results/dispute queue.
//
// Registration/check-in take a team id (TeamRef); Start is gated to guild_admin
// (platform_operator also passes) via RoleGate and the backend re-checks authz.

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import type { EntrantResponse, StartResponse, TournamentResponse } from "@lol/clients-ts";
import {
  Button,
  ErrorBanner,
  SectionHeader,
  Spinner,
  StatusBadge,
  TextInput,
} from "../../components";
import {
  FORMAT_LABELS,
  GAME_MODE_LABELS,
  TOURNAMENT_STATUS_LABELS,
  entrantsText,
  label as labelFor,
  seriesText,
} from "../../lib/labels";
import { BracketView, type BracketMatch } from "../../components/BracketView";
import { RoleGate } from "../../auth/RequireRole";
import { GUILD_ADMIN, PLATFORM_OPERATOR } from "../../auth/roles";
import { useApi } from "../../api/ApiProvider";
import { useAsyncData, useAsyncAction } from "../../api/useAsync";

const ADMIN_ROLES = [GUILD_ADMIN, PLATFORM_OPERATOR] as const;

export interface TournamentDetailProps {
  tournamentId: string;
  /**
   * Optional pre-fetched bracket matches (presentational). The current backend
   * does not expose a GET-matches route via the client, so callers that have
   * match data feed it here; otherwise the bracket renders its empty state
   * until the tournament is started.
   */
  matches?: ReadonlyArray<BracketMatch>;
  /** Optional dismiss action, rendered in the detail's own header. */
  onClose?: () => void;
}

export function TournamentDetail({
  tournamentId,
  matches = [],
  onClose,
}: TournamentDetailProps): JSX.Element {
  const api = useApi();
  const navigate = useNavigate();

  const tournament = useAsyncData<TournamentResponse>(
    () => api.tournaments.get(tournamentId),
    [tournamentId],
  );

  // Who is actually in, read from the server. This used to be component state
  // seeded only by registrations made in THIS browser session, so an admin who
  // opened the screen saw "No teams registered" no matter how full the bracket
  // was — and had no way to tell who still had to lock in before starting.
  const entrants = useAsyncData<EntrantResponse[]>(
    () => api.tournaments.entrants(tournamentId),
    [tournamentId],
  );

  const [teamId, setTeamId] = useState("");
  const [startResult, setStartResult] = useState<StartResponse | null>(null);

  const register = useAsyncAction(() =>
    api.tournaments.register(tournamentId, { team_id: teamId.trim() }),
  );
  const checkIn = useAsyncAction((id: string) =>
    api.tournaments.checkIn(tournamentId, { team_id: id }),
  );
  const start = useAsyncAction(() => api.tournaments.start(tournamentId));

  async function handleRegister(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (teamId.trim().length === 0) return;
    await register.run();
    setTeamId("");
    entrants.reload();
  }

  async function handleCheckIn(id: string): Promise<void> {
    await checkIn.run(id);
    entrants.reload();
  }

  async function handleStart(): Promise<void> {
    const result = await start.run();
    setStartResult(result);
    tournament.reload();
    entrants.reload();
  }

  function handleOverride(match: BracketMatch): void {
    // Route to the dispute/override queue (the admin escape hatch for results
    // that fail corroboration — PLAN.md §3.6.6).
    navigate(`/disputes?match=${encodeURIComponent(match.id)}`);
  }

  const t = tournament.data;
  const entered = entrants.data ?? [];
  const lockedIn = entered.filter((e) => e.locked_in).length;
  const soloMode = t?.game_mode === "aram_1v1";

  return (
    <section aria-labelledby="tournament-detail-heading" data-tournament-detail>
      <SectionHeader
        id="tournament-detail-heading"
        title={t ? t.name : "Tournament"}
        intro={
          t
            ? `${labelFor(GAME_MODE_LABELS, t.game_mode)} · ${labelFor(FORMAT_LABELS, t.format)} · ${seriesText(t.best_of)}`
            : undefined
        }
        actions={
          onClose ? (
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
          ) : undefined
        }
      />

      {tournament.loading ? <Spinner label="Loading tournament" /> : null}
      {tournament.error ? (
        <ErrorBanner error={tournament.error} onRetry={tournament.reload} />
      ) : null}

      {t ? (
        <dl data-tournament-summary>
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={t.status} labels={TOURNAMENT_STATUS_LABELS} />
            </dd>
          </div>
          <div>
            <dt>Mode</dt>
            <dd>{labelFor(GAME_MODE_LABELS, t.game_mode)}</dd>
          </div>
          <div>
            <dt>Format</dt>
            <dd>{labelFor(FORMAT_LABELS, t.format)}</dd>
          </div>
          <div>
            <dt>Series</dt>
            <dd>{seriesText(t.best_of)}</dd>
          </div>
          <div>
            <dt>Bracket size</dt>
            <dd>{entrantsText(t.max_teams, t.game_mode)}</dd>
          </div>
          {t.registration_opens_at ? (
            <div>
              <dt>Registration opens</dt>
              <dd>{new Date(t.registration_opens_at).toLocaleString()}</dd>
            </div>
          ) : null}
          {t.start_at ? (
            <div>
              <dt>Scheduled start</dt>
              <dd>{new Date(t.start_at).toLocaleString()}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      <section aria-labelledby="registrations-heading" data-registrations>
        <h3 id="registrations-heading">Entrants{entered.length > 0 ? ` (${lockedIn}/${entered.length} locked in)` : ""}</h3>

        {entrants.loading ? <Spinner label="Loading entrants" /> : null}
        {entrants.error ? (
          <ErrorBanner error={entrants.error} onRetry={entrants.reload} />
        ) : null}
        {checkIn.error ? <ErrorBanner error={checkIn.error} /> : null}

        {!entrants.loading && entered.length === 0 ? (
          <p data-no-registrations>
            Nobody has entered yet. Share the join code — {soloMode ? "players" : "team captains"}{" "}
            redeem it in the desktop client.
          </p>
        ) : (
          <ul data-registration-list>
            {entered.map((e) => (
              <li key={e.team_id} data-registration data-locked-in={e.locked_in ? "" : undefined}>
                <span data-registration-team>{e.name}</span>
                {/* Lock-in is per player, so "not ready" alone would leave an
                    admin guessing who is holding the bracket up. */}
                <span data-registration-status>
                  {e.locked_in
                    ? "locked in"
                    : e.players.length > 0
                      ? `${e.players.filter((p) => p.locked_in).length}/${e.players.length} locked in`
                      : "not ready"}
                </span>
                {e.locked_in ? null : (
                  <Button
                    variant="secondary"
                    busy={checkIn.loading}
                    onClick={() => handleCheckIn(e.team_id)}
                  >
                    Lock in
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}

        <RoleGate anyOf={ADMIN_ROLES}>
          <details data-manual-register>
            <summary>Add an entrant by team ID</summary>
            <form onSubmit={handleRegister} aria-label="Register a team">
              <TextInput
                label="Team ID"
                required
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                hint="Escape hatch — normally a captain redeems the join code instead."
              />
              {register.error ? <ErrorBanner error={register.error} /> : null}
              <Button
                type="submit"
                variant="primary"
                busy={register.loading}
                disabled={teamId.trim().length === 0 || register.loading}
              >
                Register team
              </Button>
            </form>
          </details>
        </RoleGate>
      </section>

      <RoleGate anyOf={ADMIN_ROLES}>
        <section aria-labelledby="start-heading" data-start>
          <h3 id="start-heading">Start tournament</h3>
          <p>Generates the single-elimination bracket and seeds checked-in teams.</p>
          {start.error ? <ErrorBanner error={start.error} /> : null}
          {startResult ? (
            <p data-start-result role="status">
              Bracket generated: {startResult.matches_created} matches across{" "}
              {startResult.rounds} rounds ({startResult.byes} byes).
            </p>
          ) : null}
          <Button variant="primary" busy={start.loading} onClick={handleStart}>
            Start
          </Button>
        </section>
      </RoleGate>

      <section aria-labelledby="bracket-heading" data-bracket-section>
        <h3 id="bracket-heading">Bracket</h3>
        <BracketView
          matches={matches}
          onOverride={handleOverride}
          caption={t ? `${t.name} bracket` : "Tournament bracket"}
        />
      </section>
    </section>
  );
}
